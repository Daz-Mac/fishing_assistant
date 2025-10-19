"""Sensor platform for Fishing Assistant."""
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfLength, UnitOfSpeed, PERCENTAGE
import datetime
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
)
from .score import get_fish_score_forecast, scale_score
from .tide_proxy import TideProxy
from .marine_data import MarineDataFetcher
from .ocean_scoring import OceanFishingScorer

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
        )
    )
    
    # Create tide state sensor
    if tide_proxy:
        sensors.append(
            TideStateSensor(
                hass=hass,
                config_entry=config_entry,
                tide_proxy=tide_proxy,
            )
        )
        
        sensors.append(
            TideStrengthSensor(
                hass=hass,
                config_entry=config_entry,
                tide_proxy=tide_proxy,
            )
        )
    
    # Create wave sensors
    if marine_fetcher:
        sensors.append(
            WaveHeightSensor(
                hass=hass,
                config_entry=config_entry,
                marine_fetcher=marine_fetcher,
            )
        )
        
        sensors.append(
            WavePeriodSensor(
                hass=hass,
                config_entry=config_entry,
                marine_fetcher=marine_fetcher,
            )
        )
    
    async_add_entities(sensors)


# ============================================================================
# FRESHWATER SENSORS (Original)
# ============================================================================

class FishScoreSensor(SensorEntity):
    """Sensor for freshwater fishing score."""
    
    should_poll = True

    def __init__(self, name, fish, lat, lon, body_type, timezone, elevation, config_entry_id):
        self._last_update_hour = None
        self._config_entry_id = config_entry_id
        self._device_identifier = f"{name}_{lat}_{lon}"
        self._name = f"{name.lower().replace(' ', '_')}_{fish}_score"
        self._friendly_name = f"{name} ({fish.title()}) Fishing Score"
        self._state = None
        self._attrs = {
            "fish": fish,
            "location": name,
            "lat": lat,
            "lon": lon,
            "body_type": body_type,
            "timezone": timezone,
            "elevation": elevation,
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
        now = datetime.datetime.now()
        update_hours = [0, 6, 12, 18]
        
        if self._last_update_hour is not None and now.hour not in update_hours:
            return
        
        if self._last_update_hour == now.hour:
            return

        forecast = await get_fish_score_forecast(
            hass=self.hass,
            fish=self._attrs["fish"],
            lat=self._attrs["lat"],
            lon=self._attrs["lon"],
            timezone=self._attrs["timezone"],
            elevation=self._attrs["elevation"],
            body_type=self._attrs["body_type"],
        )

        today_str = datetime.date.today().strftime("%Y-%m-%d")
        today_data = forecast.get(today_str, {})
        self._state = today_data.get("score", 0)
        self._attrs["forecast"] = forecast
        self._last_update_hour = now.hour

    async def async_added_to_hass(self):
        await self.async_update()


# ============================================================================
# OCEAN MODE SENSORS
# ============================================================================

class OceanFishingScoreSensor(SensorEntity):
    """Main ocean fishing score sensor."""
    
    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy, marine_fetcher):
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
            "latitude": lat,
            "longitude": lon,
            "mode": "ocean",
            "habitat": data.get("habitat_preset"),
            "species_focus": data.get("species_focus"),
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
        now = datetime.datetime.now()
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
            
            # Calculate score
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
            
            self._last_update_hour = now.hour
            
        except Exception as e:
            _LOGGER.error(f"Error updating ocean fishing score: {e}")
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
            astro["sunrise"] = sun_state.attributes.get("next_rising")
            astro["sunset"] = sun_state.attributes.get("next_setting")
        
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
        await self.async_update()


class TideStateSensor(SensorEntity):
    """Sensor for tide state (rising/falling/slack)."""
    
    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy):
        """Initialize the tide state sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy
        
        data = config_entry.data
        name = data["name"]
        
        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_tide_state"
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

    def __init__(self, hass, config_entry, tide_proxy):
        """Initialize the tide strength sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy
        
        data = config_entry.data
        name = data["name"]
        
        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_tide_strength"
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

    def __init__(self, hass, config_entry, marine_fetcher):
        """Initialize the wave height sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._marine_fetcher = marine_fetcher
        
        data = config_entry.data
        name = data["name"]
        
        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_wave_height"
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

    def __init__(self, hass, config_entry, marine_fetcher):
        """Initialize the wave period sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._marine_fetcher = marine_fetcher
        
        data = config_entry.data
        name = data["name"]
        
        self._device_identifier = f"{name}_{data['latitude']}_{data['longitude']}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_wave_period"
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
