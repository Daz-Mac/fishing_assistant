"""Sensor platform for Fishing Assistant."""
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfLength, UnitOfSpeed, PERCENTAGE
from homeassistant.util import dt as dt_util
from datetime import datetime, timedelta
import logging

from .const import (
    DOMAIN,
    CONF_MODE,
    MODE_FRESHWATER,
    MODE_OCEAN,
    CONF_WEATHER_ENTITY,
    CONF_MARINE_ENABLED,
    CONF_TIDE_MODE,
    TIDE_MODE_PROXY,
    CONF_TIME_PERIODS,
    PERIOD_FULL_DAY,
)
from .score import get_fish_score_forecast, get_fish_score
from .species_loader import SpeciesLoader
from .tide_proxy import TideProxy
from .marine_data import MarineDataFetcher
from .ocean_scoring import OceanFishingScorer
from .helpers.astro import calculate_astronomy_forecast

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities
):
    """Set up fishing assistant sensors from a config entry."""
    data = config_entry.data
    mode = data.get(CONF_MODE, MODE_FRESHWATER)

    if mode == MODE_OCEAN:
        await _setup_ocean_sensors(hass, config_entry, async_add_entities)
    else:
        await _setup_freshwater_sensors(hass, config_entry, async_add_entities)


async def _setup_freshwater_sensors(hass, config_entry, async_add_entities):
    """Set up freshwater fishing sensors (original)."""
    data = config_entry.data
    sensors = []

    name = data["name"]
    lat = data["latitude"]
    lon = data["longitude"]
    fish_list = data["fish"]
    body_type = data["body_type"]
    timezone = data["timezone"]
    elevation = data["elevation"]
    period_type = data.get(CONF_TIME_PERIODS, PERIOD_FULL_DAY)
    weather_entity = data.get(CONF_WEATHER_ENTITY)

    # Initialize species loader
    species_loader = SpeciesLoader(hass)
    await species_loader.async_load_profiles()

    for fish in fish_list:
        sensors.append(
            FishScoreSensor(
                name=name,
                fish=fish,
                lat=lat,
                lon=lon,
                timezone=timezone,
                body_type=body_type,
                elevation=elevation,
                period_type=period_type,
                weather_entity=weather_entity,
                species_loader=species_loader,
                config_entry_id=config_entry.entry_id
            )
        )

    async_add_entities(sensors)


async def _setup_ocean_sensors(hass, config_entry, async_add_entities):
    """Set up ocean fishing sensors."""
    data = config_entry.data
    sensors = []

    name = data["name"]
    lat = data["latitude"]
    lon = data["longitude"]
    weather_entity = data.get(CONF_WEATHER_ENTITY)

    # Create a location key based on coordinates for sensor naming consistency
    # This ensures all sensors at the same location share the same base name
    location_key = f"{name.lower().replace(' ', '_')}"

    # Initialize data fetchers
    tide_proxy = None
    marine_fetcher = None

    if data.get(CONF_TIDE_MODE) == TIDE_MODE_PROXY:
        tide_proxy = TideProxy(hass, lat, lon)

    if data.get(CONF_MARINE_ENABLED, True):
        marine_fetcher = MarineDataFetcher(hass, lat, lon)

    # Create main ocean fishing score sensor
    sensors.append(
        OceanFishingScoreSensor(
            hass=hass,
            config_entry=config_entry,
            tide_proxy=tide_proxy,
            marine_fetcher=marine_fetcher,
            location_key=location_key,
        )
    )

    # Create tide state sensor
    if tide_proxy:
        sensors.append(
            TideStateSensor(
                hass=hass,
                config_entry=config_entry,
                tide_proxy=tide_proxy,
                location_key=location_key,
            )
        )
        sensors.append(
            TideStrengthSensor(
                hass=hass,
                config_entry=config_entry,
                tide_proxy=tide_proxy,
                location_key=location_key,
            )
        )

    # Create wave sensors
    if marine_fetcher:
        sensors.append(
            WaveHeightSensor(
                hass=hass,
                config_entry=config_entry,
                marine_fetcher=marine_fetcher,
                location_key=location_key,
            )
        )
        sensors.append(
            WavePeriodSensor(
                hass=hass,
                config_entry=config_entry,
                marine_fetcher=marine_fetcher,
                location_key=location_key,
            )
        )

    # Create wind sensors
    if weather_entity:
        sensors.append(
            WindSpeedSensor(
                hass=hass,
                config_entry=config_entry,
                location_key=location_key,
            )
        )
        sensors.append(
            WindGustSensor(
                hass=hass,
                config_entry=config_entry,
                location_key=location_key,
            )
        )

    async_add_entities(sensors)


# ============================================================================
# FRESHWATER SENSORS (Original)
# ============================================================================

class FishScoreSensor(SensorEntity):
    """Sensor for freshwater fishing score."""

    should_poll = True

    def __init__(self, name, fish, lat, lon, body_type, timezone, elevation, period_type, weather_entity, species_loader, config_entry_id):
        self._last_update_hour = None
        self._config_entry_id = config_entry_id
        self._device_identifier = f"{name}_{lat}_{lon}"
        self._name = f"{name.lower().replace(' ', '_')}_{fish}_score"
        self._friendly_name = f"{name} ({fish.title()}) Fishing Score"
        self._state = None
        self._species_loader = species_loader
        self._attrs = {
            "fish": fish,
            "location": name,
            "lat": lat,
            "lon": lon,
            "body_type": body_type,
            "timezone": timezone,
            "elevation": elevation,
            "period_type": period_type,
            "weather_entity": weather_entity,
        }

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def device_class(self):
        return None

    @property
    def entity_category(self):
        return None

    @property
    def icon(self):
        return "mdi:fish"

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attrs
    
    @property
    def native_unit_of_measurement(self):
        return "/10"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._attrs["location"],
            "manufacturer": "Fishing Assistant",
            "model": "Fish Score Sensor",
            "entry_type": "service",
            "via_device": None
        }

    async def async_update(self):
        """Fetch the 7-day forecast and set today's score as state."""
        now = datetime.now()
        update_hours = [0, 6, 12, 18]
        
        if self._last_update_hour is not None and now.hour not in update_hours:
            return
        
        if self._last_update_hour == now.hour:
            return
        
        weather_entity_id = self._attrs.get("weather_entity")
        if not weather_entity_id:
            _LOGGER.error("No weather entity configured for freshwater sensor")
            return
        
        try:
            # Get current weather data for immediate score calculation
            weather_state = self.hass.states.get(weather_entity_id)
            if not weather_state:
                _LOGGER.error("Weather entity not available: %s", weather_entity_id)
                return
            
            weather_data = {
                "temperature": weather_state.attributes.get("temperature"),
                "wind_speed": weather_state.attributes.get("wind_speed", 0),
                "cloud_coverage": weather_state.attributes.get("cloud_coverage", 50),
                "pressure": weather_state.attributes.get("pressure", 1013),
            }
            
            # Get current astro data
            astro_forecast = await calculate_astronomy_forecast(
                self.hass,
                self._attrs["lat"],
                self._attrs["lon"],
                days=1
            )
            
            today_str = now.date().strftime("%Y-%m-%d")
            astro_data = astro_forecast.get(today_str, {})
            
            # Calculate current score with component breakdown
            current_result = get_fish_score(
                hass=self.hass,
                fish_list=[self._attrs["fish"]],
                body_type=self._attrs["body_type"],
                weather_data=weather_data,
                astro_data=astro_data,
                species_loader=self._species_loader,
                target_time=now,
            )
            
            # Set current score and component scores
            self._state = current_result.get("score", 0)
            self._attrs["component_scores"] = current_result.get("component_scores", {})
            
            # Get 7-day forecast
            forecast = await get_fish_score_forecast(
                hass=self.hass,
                fish_list=[self._attrs["fish"]],
                body_type=self._attrs["body_type"],
                weather_entity_id=weather_entity_id,
                latitude=self._attrs["lat"],
                longitude=self._attrs["lon"],
                species_loader=self._species_loader,
                period_type=self._attrs["period_type"],
                days=7,
            )
            
            self._attrs["forecast"] = forecast
            self._last_update_hour = now.hour
            
            _LOGGER.debug(
                "Updated %s: score=%s, component_scores=%s, forecast_days=%d",
                self._name,
                self._state,
                self._attrs.get("component_scores"),
                len(forecast)
            )
            
        except Exception as e:
            _LOGGER.error("Error updating freshwater sensor %s: %s", self._name, e, exc_info=True)
            self._state = None


# ============================================================================
# OCEAN MODE SENSORS
# ============================================================================

class OceanFishingScoreSensor(SensorEntity):
    """Main ocean fishing score sensor."""

    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy, marine_fetcher, location_key):
        """Initialize the ocean fishing score sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy
        self._marine_fetcher = marine_fetcher
        self._scorer = OceanFishingScorer(hass, config_entry.data)

        data = config_entry.data
        name = data["name"]
        lat = data["latitude"]
        lon = data["longitude"]

        self._device_identifier = f"{name}_{lat}_{lon}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_ocean_fishing_score"
        self._friendly_name = f"{name} Ocean Fishing Score"
        self._state = None
        self._last_update_hour = None

        self._attrs = {
            "location": name,
            "location_key": location_key,  # Technical key for sensor lookups
            "latitude": lat,
            "longitude": lon,
            "mode": "ocean",
            "habitat": data.get("habitat_preset"),
            "species_focus": "Unknown",  # Will be updated after scorer initializes
        }

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        if self._state is None:
            return "mdi:waves"
        elif self._state >= 8:
            return "mdi:fish"
        elif self._state >= 6:
            return "mdi:fish-off"
        else:
            return "mdi:waves"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return "/10"

    @property
    def extra_state_attributes(self):
        return self._attrs

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._attrs["location"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
            "entry_type": "service",
        }

    async def async_update(self):
        """Update the fishing score."""
        now = datetime.now()
        update_hours = [0, 6, 12, 18]

        # Only update at specific hours
        if self._last_update_hour is not None and now.hour not in update_hours:
            return

        if self._last_update_hour == now.hour:
            return

        try:
            # Gather all data
            weather_data = await self._get_weather_data()
            tide_data = await self._tide_proxy.get_tide_data() if self._tide_proxy else {}
            marine_data = await self._marine_fetcher.get_marine_data() if self._marine_fetcher else {}
            astro_data = await self._get_astro_data()

            # Calculate current score
            result = self._scorer.calculate_score(
                weather_data=weather_data,
                tide_data=tide_data,
                marine_data=marine_data,
                astro_data=astro_data,
            )

            self._state = result["score"]
            self._attrs.update({
                "safety": result.get("safety"),
                "tide_state": result.get("tide_state"),
                "best_window": result.get("best_window"),
                "conditions_summary": result.get("conditions_summary"),
                "breakdown": result.get("breakdown"),
                "last_updated": now.isoformat(),
            })

            # Calculate 5-day forecast
            weather_entity_id = self._config_entry.data.get(CONF_WEATHER_ENTITY)
            if weather_entity_id:
                forecast = await self._scorer.calculate_forecast(
                    weather_entity_id=weather_entity_id,
                    marine_data=marine_data,
                    days=5,
                )
                self._attrs["forecast"] = forecast

            self._last_update_hour = now.hour

        except Exception as e:
            _LOGGER.error(f"Error updating ocean fishing score: {e}", exc_info=True)
            self._state = None

    async def _get_weather_data(self):
        """Get weather data from configured weather entity."""
        weather_entity_id = self._config_entry.data.get(CONF_WEATHER_ENTITY)
        if not weather_entity_id:
            return {}

        weather_state = self.hass.states.get(weather_entity_id)
        if not weather_state:
            return {}

        attrs = weather_state.attributes
        return {
            "temperature": attrs.get("temperature"),
            "wind_speed": attrs.get("wind_speed", 0),
            "wind_gust": attrs.get("wind_gust_speed", attrs.get("wind_speed", 0)),
            "cloud_cover": attrs.get("cloud_coverage", 50),
            "precipitation_probability": attrs.get("precipitation_probability", 0),
            "pressure": attrs.get("pressure", 1013),
        }

    async def _get_astro_data(self):
        """Get astronomical data from Home Assistant."""
        sun_state = self.hass.states.get("sun.sun")
        moon_state = self.hass.states.get("sensor.moon")

        astro = {}

        if sun_state:
            # Parse string timestamps to datetime objects
            sunrise_str = sun_state.attributes.get("next_rising")
            sunset_str = sun_state.attributes.get("next_setting")
            
            if sunrise_str:
                astro["sunrise"] = dt_util.parse_datetime(sunrise_str)
            if sunset_str:
                astro["sunset"] = dt_util.parse_datetime(sunset_str)

        if moon_state:
            phase_name = moon_state.state
            phase_map = {
                "new_moon": 0.0,
                "waxing_crescent": 0.125,
                "first_quarter": 0.25,
                "waxing_gibbous": 0.375,
                "full_moon": 0.5,
                "waning_gibbous": 0.625,
                "last_quarter": 0.75,
                "waning_crescent": 0.875,
            }
            astro["moon_phase"] = phase_map.get(phase_name, 0.5)

        return astro

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        # Initialize the scorer asynchronously
        await self._scorer.async_initialize()
        
        # Update species_focus with the actual loaded species name
        if self._scorer.species_profile:
            self._attrs["species_focus"] = self._scorer.species_profile.get("name", "Unknown")
        
        await self.async_update()


class TideStateSensor(SensorEntity):
    """Sensor for tide state (rising/falling/slack)."""

    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy, location_key):
        """Initialize the tide state sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_tide_state"
        self._friendly_name = f"{name} Tide State"
        self._state = None
        self._attrs = {}

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        if self._state == "rising":
            return "mdi:arrow-up-bold"
        elif self._state == "falling":
            return "mdi:arrow-down-bold"
        else:
            return "mdi:minus"

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attrs

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update tide state."""
        try:
            tide_data = await self._tide_proxy.get_tide_data()
            self._state = tide_data.get("state", "unknown")
            self._attrs = {
                "next_high": tide_data.get("next_high"),
                "next_low": tide_data.get("next_low"),
                "source": tide_data.get("source"),
            }
        except Exception as e:
            _LOGGER.error(f"Error updating tide state: {e}")
            self._state = "unknown"

    async def async_added_to_hass(self):
        await self.async_update()


class TideStrengthSensor(SensorEntity):
    """Sensor for tide strength (spring vs neap)."""

    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy, location_key):
        """Initialize the tide strength sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_tide_strength"
        self._friendly_name = f"{name} Tide Strength"
        self._state = None

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        return "mdi:gauge"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update tide strength."""
        try:
            tide_data = await self._tide_proxy.get_tide_data()
            self._state = tide_data.get("strength", 50)
        except Exception as e:
            _LOGGER.error(f"Error updating tide strength: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()


class WaveHeightSensor(SensorEntity):
    """Sensor for wave height."""

    should_poll = True

    def __init__(self, hass, config_entry, marine_fetcher, location_key):
        """Initialize the wave height sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._marine_fetcher = marine_fetcher

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_wave_height"
        self._friendly_name = f"{name} Wave Height"
        self._state = None
        self._attrs = {}

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        return "mdi:wave"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return UnitOfLength.METERS

    @property
    def device_class(self):
        return SensorDeviceClass.DISTANCE

    @property
    def extra_state_attributes(self):
        return self._attrs

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update wave height."""
        try:
            marine_data = await self._marine_fetcher.get_marine_data()
            current = marine_data.get("current", {})
            self._state = current.get("wave_height")
            self._attrs = {
                "wind_wave_height": current.get("wind_wave_height"),
                "swell_wave_height": current.get("swell_wave_height"),
                "wave_direction": current.get("wave_direction"),
            }
        except Exception as e:
            _LOGGER.error(f"Error updating wave height: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()


class WavePeriodSensor(SensorEntity):
    """Sensor for wave period."""

    should_poll = True

    def __init__(self, hass, config_entry, marine_fetcher, location_key):
        """Initialize the wave period sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._marine_fetcher = marine_fetcher

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_wave_period"
        self._friendly_name = f"{name} Wave Period"
        self._state = None

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        return "mdi:sine-wave"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return "s"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update wave period."""
        try:
            marine_data = await self._marine_fetcher.get_marine_data()
            current = marine_data.get("current", {})
            self._state = current.get("wave_period")
        except Exception as e:
            _LOGGER.error(f"Error updating wave period: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()


class WindSpeedSensor(SensorEntity):
    """Sensor for wind speed."""

    should_poll = True

    def __init__(self, hass, config_entry, location_key):
        """Initialize the wind speed sensor."""
        self.hass = hass
        self._config_entry = config_entry

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_wind_speed"
        self._friendly_name = f"{name} Wind Speed"
        self._state = None

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        if self._state is None:
            return "mdi:weather-windy"
        elif self._state < 10:
            return "mdi:weather-windy"
        elif self._state < 20:
            return "mdi:weather-windy-variant"
        else:
            return "mdi:weather-hurricane"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return UnitOfSpeed.KILOMETERS_PER_HOUR

    @property
    def device_class(self):
        return SensorDeviceClass.WIND_SPEED

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update wind speed."""
        try:
            weather_entity_id = self._config_entry.data.get(CONF_WEATHER_ENTITY)
            if not weather_entity_id:
                return

            weather_state = self.hass.states.get(weather_entity_id)
            if not weather_state:
                return

            self._state = weather_state.attributes.get("wind_speed")
        except Exception as e:
            _LOGGER.error(f"Error updating wind speed: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()


class WindGustSensor(SensorEntity):
    """Sensor for wind gust speed."""

    should_poll = True

    def __init__(self, hass, config_entry, location_key):
        """Initialize the wind gust sensor."""
        self.hass = hass
        self._config_entry = config_entry

        data = config_entry.data
        name = data["name"]

        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{location_key}_wind_gust"
        self._friendly_name = f"{name} Wind Gust"
        self._state = None

    @property
    def name(self):
        return self._friendly_name

    @property
    def unique_id(self):
        return self._name

    @property
    def icon(self):
        if self._state is None:
            return "mdi:weather-windy"
        elif self._state < 15:
            return "mdi:weather-windy"
        elif self._state < 30:
            return "mdi:weather-windy-variant"
        else:
            return "mdi:weather-hurricane"

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return UnitOfSpeed.KILOMETERS_PER_HOUR

    @property
    def device_class(self):
        return SensorDeviceClass.WIND_SPEED

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_identifier)},
            "name": self._config_entry.data["name"],
            "manufacturer": "Fishing Assistant",
            "model": "Ocean Fishing Score",
        }

    async def async_update(self):
        """Update wind gust speed."""
        try:
            weather_entity_id = self._config_entry.data.get(CONF_WEATHER_ENTITY)
            if not weather_entity_id:
                return

            weather_state = self.hass.states.get(weather_entity_id)
            if not weather_state:
                return

            # Try wind_gust_speed first, fallback to wind_speed
            self._state = weather_state.attributes.get(
                "wind_gust_speed",
                weather_state.attributes.get("wind_speed")
            )
        except Exception as e:
            _LOGGER.error(f"Error updating wind gust: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()