"""Sensor platform for Fishing Assistant."""
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfLength, UnitOfSpeed, PERCENTAGE
from homeassistant.util import dt as dt_util
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

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
    CONF_SPECIES_ID,
    CONF_HABITAT_PRESET,
    CONF_USE_OPEN_METEO,
)
from .score import FreshwaterFishingScorer
from .ocean_scoring import OceanFishingScorer
from .species_loader import SpeciesLoader
from .tide_proxy import TideProxy
from .marine_data import MarineDataFetcher
from .weather_fetcher import WeatherFetcher
from .data_formatter import DataFormatter
from .api import OpenMeteoClient

_LOGGER = logging.getLogger(__name__)


class OpenMeteoAdapter:
    """
    Adapter to expose a small, defensive interface compatible with the WeatherFetcher expectations.

    It wraps the OpenMeteoClient (which returns normalized hourly items) and exposes:
      - async get_forecast(days) -> dict keyed by ISO date with normalized daily values
      - async get_current() -> dict with normalized current weather fields

    Normalizations applied:
      - temperature from temperature_2m (avg)
      - wind_speed from wind_speed_10m (m/s -> km/h)
      - wind_gust estimated from available gust key or set to 1.2 * wind_speed
      - cloud_cover from cloudcover
      - precipitation_probability = percent of hours with precipitation > 0
      - pressure from pressure_msl (hPa)
    """

    def __init__(self, client: OpenMeteoClient, latitude: float, longitude: float, include_marine: bool = False):
        self._client = client
        self._lat = latitude
        self._lon = longitude
        self._include_marine = include_marine

    async def get_forecast(self, days: int = 7) -> Dict[str, Dict]:
        hourly = await self._client.fetch_hourly_forecast(
            self._lat, self._lon, include_marine=self._include_marine, forecast_days=days
        )
        if not hourly or not isinstance(hourly, list):
            return {}

        per_day: Dict[str, Dict[str, Any]] = {}
        counts: Dict[str, int] = {}

        for item in hourly:
            t = item.get("time")
            dt = dt_util.parse_datetime(t) if t else None
            if dt is None:
                continue
            # normalize to UTC date string
            date_key = dt.astimezone(timezone.utc).date().isoformat()

            temp = item.get("temperature_2m")
            wind_ms = item.get("wind_speed_10m")  # m/s
            gust_ms = item.get("wind_gust_10m") or item.get("wind gust") or item.get("windgusts_10m")
            cloud = item.get("cloudcover") or item.get("cloud_cover")
            precip = item.get("precipitation") or item.get("rain") or item.get("precip")
            pressure = item.get("pressure_msl") or item.get("pressure")

            # initialize
            if date_key not in per_day:
                per_day[date_key] = {
                    "temperature": 0.0,
                    "wind_speed": 0.0,
                    "wind_gust": 0.0,
                    "cloud_cover": 0.0,
                    "precip_hours": 0,
                    "pressure": 0.0,
                }
                counts[date_key] = 0

            counts[date_key] += 1
            entry = per_day[date_key]

            if temp is not None:
                try:
                    entry["temperature"] += float(temp)
                except Exception:
                    pass

            if wind_ms is not None:
                try:
                    entry["wind_speed"] += float(wind_ms) * 3.6  # m/s -> km/h
                except Exception:
                    pass

            # gust: prefer explicit gust if present (m/s -> km/h)
            if gust_ms is not None:
                try:
                    entry["wind_gust"] = max(entry["wind_gust"], float(gust_ms) * 3.6)
                except Exception:
                    pass

            if cloud is not None:
                try:
                    entry["cloud_cover"] += float(cloud)
                except Exception:
                    pass

            if precip is not None:
                try:
                    if float(precip) > 0:
                        entry["precip_hours"] += 1
                except Exception:
                    pass

            if pressure is not None:
                try:
                    entry["pressure"] += float(pressure)
                except Exception:
                    pass

        # Finalize daily aggregates and map to expected keys
        final: Dict[str, Dict] = {}
        sorted_dates = sorted(per_day.keys())[:days]
        for d in sorted_dates:
            cnt = counts.get(d, 1) or 1
            e = per_day[d]

            avg_temp = e["temperature"] / cnt if cnt else None
            avg_wind = e["wind_speed"] / cnt if cnt else None
            gust = e["wind_gust"]
            if not gust and avg_wind is not None:
                # estimate gust as 1.2x average wind if no explicit gust provided
                gust = avg_wind * 1.2

            avg_cloud = e["cloud_cover"] / cnt if cnt else None
            precip_pct = int(round((e["precip_hours"] / cnt) * 100)) if cnt else 0
            avg_pressure = e["pressure"] / cnt if cnt else None

            final[d] = {
                "temperature": float(avg_temp) if avg_temp is not None else None,
                "wind_speed": float(avg_wind) if avg_wind is not None else None,
                "wind_gust": float(gust) if gust is not None else None,
                "cloud_cover": int(round(avg_cloud)) if avg_cloud is not None else None,
                "precipitation_probability": int(precip_pct),
                "pressure": float(avg_pressure) if avg_pressure is not None else None,
            }

        return final

    async def get_current(self) -> Optional[Dict]:
        hourly = await self._client.fetch_hourly_forecast(
            self._lat, self._lon, include_marine=self._include_marine, forecast_days=1
        )
        if not hourly or not isinstance(hourly, list):
            return None

        # Find the hourly entry closest to now (UTC)
        now = datetime.now(timezone.utc)
        best = None
        best_delta = None
        for item in hourly:
            t = item.get("time")
            dt = dt_util.parse_datetime(t) if t else None
            if dt is None:
                continue
            dt = dt.astimezone(timezone.utc)
            delta = abs((dt - now).total_seconds())
            if best is None or delta < best_delta:
                best = item
                best_delta = delta

        if not best:
            return None

        temp = best.get("temperature_2m")
        wind_ms = best.get("wind_speed_10m")
        gust_ms = best.get("wind_gust_10m") or best.get("windgusts_10m")
        cloud = best.get("cloudcover") or best.get("cloud_cover")
        precip = best.get("precipitation") or best.get("rain") or best.get("precip")
        pressure = best.get("pressure_msl") or best.get("pressure")

        try:
            temperature = float(temp) if temp is not None else None
        except Exception:
            temperature = None

        try:
            wind_speed = float(wind_ms) * 3.6 if wind_ms is not None else None
        except Exception:
            wind_speed = None

        try:
            wind_gust = float(gust_ms) * 3.6 if gust_ms is not None else (wind_speed * 1.2 if wind_speed else None)
        except Exception:
            wind_gust = wind_speed * 1.2 if wind_speed else None

        try:
            cloud_cover = int(round(float(cloud))) if cloud is not None else None
        except Exception:
            cloud_cover = None

        try:
            precipitation_probability = 100 if (precip is not None and float(precip) > 0) else 0
        except Exception:
            precipitation_probability = 0

        try:
            pressure_val = float(pressure) if pressure is not None else None
        except Exception:
            pressure_val = None

        return {
            "temperature": temperature,
            "wind_speed": wind_speed,
            "wind_gust": wind_gust,
            "cloud_cover": cloud_cover,
            "precipitation_probability": precipitation_probability,
            "pressure": pressure_val,
        }


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
    """Set up freshwater fishing sensors."""
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

    # Open-Meteo usage flag (default True per user request)
    use_open_meteo = data.get(CONF_USE_OPEN_METEO, True)
    open_meteo_client = None
    open_meteo_adapter = None
    if use_open_meteo:
        client = OpenMeteoClient()
        open_meteo_client = client
        open_meteo_adapter = OpenMeteoAdapter(client, lat, lon, include_marine=False)

    # Initialize species loader
    species_loader = SpeciesLoader(hass)
    await species_loader.async_load_profiles()

    # Initialize weather fetcher with weather_entity parameter and optional Open-Meteo adapter
    weather_fetcher = WeatherFetcher(
        hass,
        lat,
        lon,
        weather_entity=weather_entity,
        use_open_meteo=use_open_meteo,
        open_meteo_client=open_meteo_adapter,
    )

    for fish in fish_list:
        sensors.append(
            FishScoreSensor(
                hass=hass,
                name=name,
                fish=fish,
                lat=lat,
                lon=lon,
                timezone=timezone,
                body_type=body_type,
                elevation=elevation,
                period_type=period_type,
                weather_entity=weather_entity,
                weather_fetcher=weather_fetcher,
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

    # Open-Meteo usage flag (default True)
    use_open_meteo = data.get(CONF_USE_OPEN_METEO, True)
    open_meteo_client = None
    open_meteo_adapter = None
    if use_open_meteo:
        client = OpenMeteoClient()
        open_meteo_client = client
        open_meteo_adapter = OpenMeteoAdapter(client, lat, lon, include_marine=data.get(CONF_MARINE_ENABLED, True))

    # Create a location key based on coordinates for sensor naming consistency
    location_key = f"{name.lower().replace(' ', '_')}"

    # Initialize data fetchers with weather_entity parameter
    tide_proxy = None
    marine_fetcher = None
    weather_fetcher = WeatherFetcher(
        hass,
        lat,
        lon,
        weather_entity=weather_entity,
        use_open_meteo=use_open_meteo,
        open_meteo_client=open_meteo_adapter,
    )

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
            weather_fetcher=weather_fetcher,
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
    sensors.append(
        WindSpeedSensor(
            hass=hass,
            config_entry=config_entry,
            weather_fetcher=weather_fetcher,
            location_key=location_key,
        )
    )
    sensors.append(
        WindGustSensor(
            hass=hass,
            config_entry=config_entry,
            weather_fetcher=weather_fetcher,
            location_key=location_key,
        )
    )

    async_add_entities(sensors)


# ============================================================================
# FRESHWATER SENSORS
# ============================================================================

class FishScoreSensor(SensorEntity):
    """Sensor for freshwater fishing score."""

    should_poll = True

    def __init__(self, hass, name, fish, lat, lon, body_type, timezone, elevation, period_type, weather_entity, weather_fetcher, species_loader, config_entry_id):
        self.hass = hass
        self._last_update_hour = None
        self._config_entry_id = config_entry_id
        self._device_identifier = f"{name}_{lat}_{lon}"
        self._name = f"{name.lower().replace(' ', '_')}_{fish}_score"
        self._friendly_name = f"{name} ({fish.title()}) Fishing Score"
        self._state = None
        self._species_loader = species_loader
        self._weather_fetcher = weather_fetcher

        # Get species profile
        species_profile = species_loader.get_species(fish)
        species_profiles = {fish: species_profile} if species_profile else {}

        # Initialize the scorer with correct signature
        self._scorer = FreshwaterFishingScorer(
            latitude=lat,
            longitude=lon,
            species=[fish],
            species_profiles=species_profiles,
        )

        self._attrs = {
            "fish": fish,
            "location": name,
            "lat": lat,
            "lon": lon,
            "body_type": body_type,
            "habitat": body_type,
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
        """Fetch the current score and forecast."""
        now = datetime.now()
        update_hours = [0, 6, 12, 18]

        if self._last_update_hour is not None and now.hour not in update_hours:
            return

        if self._last_update_hour == now.hour:
            return

        try:
            # Get current weather data from HA weather entity (raw data)
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            if not weather_data_raw:
                _LOGGER.error("No weather data available for freshwater sensor")
                return

            # Get current astro data (already in correct format)
            astro_data = await self._get_astro_data()

            # Calculate current score - pass raw data, scorer will format internally
            result = self._scorer.calculate_score(
                weather_data=weather_data_raw,
                astro_data=astro_data,
                current_time=now,
            )

            # Result is already formatted by the scorer
            self._state = result["score"]
            self._attrs.update({
                "breakdown": result.get("breakdown", {}),
                "component_scores": result.get("component_scores", {}),
                "rating": result.get("rating"),
                "last_updated": now.isoformat(),
            })

            # Get forecast data
            forecast_raw = await self._weather_fetcher.get_forecast(days=7)
            if forecast_raw:
                # Convert forecast dict to list format expected by scorer
                forecast_list = []
                for date_str, data in forecast_raw.items():
                    # Parse the date string
                    try:
                        forecast_date = datetime.fromisoformat(date_str)
                    except Exception:
                        forecast_date = dt_util.parse_datetime(date_str)
                    # Add datetime to the raw data
                    data["datetime"] = forecast_date
                    forecast_list.append(data)

                # Calculate forecast scores - pass raw data
                forecast_scores = await self._scorer.calculate_forecast(
                    weather_forecast=forecast_list,
                )
                # Forecast is already formatted by the scorer
                self._attrs["forecast"] = forecast_scores

            self._last_update_hour = now.hour

            _LOGGER.debug(
                "Updated %s: score=%s, component_scores=%s",
                self._name,
                self._state,
                self._attrs.get("component_scores"),
            )

        except Exception as e:
            _LOGGER.error("Error updating freshwater sensor %s: %s", self._name, e, exc_info=True)
            self._state = None

    async def _get_astro_data(self):
        """Get astronomical data from Home Assistant."""
        sun_state = self.hass.states.get("sun.sun")
        moon_state = self.hass.states.get("sensor.moon")

        astro = {}

        if sun_state:
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


# ============================================================================
# OCEAN MODE SENSORS
# ============================================================================

class OceanFishingScoreSensor(SensorEntity):
    """Main ocean fishing score sensor."""

    should_poll = True

    def __init__(self, hass, config_entry, tide_proxy, marine_fetcher, weather_fetcher, location_key):
        """Initialize the ocean fishing score sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._tide_proxy = tide_proxy
        self._marine_fetcher = marine_fetcher
        self._weather_fetcher = weather_fetcher

        data = config_entry.data
        name = data["name"]
        lat = data["latitude"]
        lon = data["longitude"]
        species_id = data.get(CONF_SPECIES_ID, "general_mixed")

        # Initialize species loader
        species_loader = SpeciesLoader(hass)

        # Initialize the ocean scorer with correct signature
        self._scorer = OceanFishingScorer(
            latitude=lat,
            longitude=lon,
            species=[species_id],
            species_profiles={},
            hass=hass,
            config=data
        )

        self._device_identifier = f"{name}_{lat}_{lon}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_ocean_fishing_score"
        self._friendly_name = f"{name} Ocean Fishing Score"
        self._state = None
        self._last_update_hour = None

        self._attrs = {
            "location": name,
            "location_key": location_key,
            "latitude": lat,
            "longitude": lon,
            "mode": "ocean",
            "habitat": data.get(CONF_HABITAT_PRESET),
            "species_focus": species_id,
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
            # Gather all raw data
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            tide_data_raw = await self._tide_proxy.get_tide_data() if self._tide_proxy else None
            marine_data_raw = await self._marine_fetcher.get_marine_data() if self._marine_fetcher else None
            astro_data = await self._get_astro_data()

            if not weather_data_raw:
                _LOGGER.error("No weather data available for ocean sensor")
                return

            # Calculate current score - pass raw data, scorer will format internally
            result = self._scorer.calculate_score(
                weather_data=weather_data_raw,
                astro_data=astro_data,
                tide_data=tide_data_raw,
                marine_data=marine_data_raw,
                current_time=now,
            )

            # Result is already formatted by the scorer
            self._state = result["score"]
            self._attrs.update({
                "rating": result.get("rating"),
                "breakdown": result.get("breakdown", {}),
                "component_scores": result.get("component_scores", {}),
                "last_updated": now.isoformat(),
            })

            # Add tide state if available
            if tide_data_raw:
                tide_data = DataFormatter.format_tide_data(tide_data_raw)
                self._attrs["tide_state"] = tide_data.get("state")

            # Check safety - format data for safety check
            weather_formatted = DataFormatter.format_weather_data(weather_data_raw)
            marine_formatted = DataFormatter.format_marine_data(marine_data_raw) if marine_data_raw else {}
            safety_status, safety_reasons = self._scorer.check_safety(weather_formatted, marine_formatted)
            self._attrs["safety"] = {
                "status": safety_status,
                "reasons": safety_reasons
            }

            # Calculate forecast
            forecast_raw = await self._weather_fetcher.get_forecast(days=7)
            if forecast_raw:
                # Convert forecast dict to list format
                forecast_list = []
                for date_str, data in forecast_raw.items():
                    try:
                        forecast_date = datetime.fromisoformat(date_str)
                    except Exception:
                        forecast_date = dt_util.parse_datetime(date_str)
                    data["datetime"] = forecast_date
                    forecast_list.append(data)

                # Get tide and marine forecasts if available
                tide_forecast = None
                marine_forecast = None

                if tide_data_raw and "forecast" in tide_data_raw:
                    tide_forecast = tide_data_raw["forecast"]

                if marine_data_raw and "forecast" in marine_data_raw:
                    marine_forecast = marine_data_raw["forecast"]

                # Calculate forecast scores - pass raw data
                forecast_scores = await self._scorer.calculate_forecast(
                    weather_forecast=forecast_list,
                    tide_forecast=tide_forecast,
                    marine_forecast=marine_forecast,
                )

                # Forecast is already formatted by the scorer
                self._attrs["forecast"] = forecast_scores

            self._last_update_hour = now.hour

            _LOGGER.debug(
                "Updated %s: score=%s, component_scores=%s",
                self._name,
                self._state,
                self._attrs.get("component_scores"),
            )

        except Exception as e:
            _LOGGER.error(f"Error updating ocean fishing score: {e}", exc_info=True)
            self._state = None

    async def _get_astro_data(self):
        """Get astronomical data from Home Assistant."""
        sun_state = self.hass.states.get("sun.sun")
        moon_state = self.hass.states.get("sensor.moon")

        astro = {}

        if sun_state:
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
            tide_data_raw = await self._tide_proxy.get_tide_data()
            if not tide_data_raw:
                _LOGGER.warning("No tide data available")
                self._state = "unknown"
                return

            tide_data = DataFormatter.format_tide_data(tide_data_raw)

            self._state = tide_data.get("state", "unknown")
            self._attrs = {
                "next_high": tide_data.get("next_high"),
                "next_low": tide_data.get("next_low"),
                "strength": tide_data.get("strength"),
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
            tide_data_raw = await self._tide_proxy.get_tide_data()
            if not tide_data_raw:
                _LOGGER.warning("No tide data available")
                self._state = None
                return

            tide_data = DataFormatter.format_tide_data(tide_data_raw)
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
            marine_data_raw = await self._marine_fetcher.get_marine_data()
            if not marine_data_raw:
                _LOGGER.warning("No marine data available")
                self._state = None
                return

            marine_data = DataFormatter.format_marine_data(marine_data_raw)

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
            marine_data_raw = await self._marine_fetcher.get_marine_data()
            if not marine_data_raw:
                _LOGGER.warning("No marine data available")
                self._state = None
                return

            marine_data = DataFormatter.format_marine_data(marine_data_raw)

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

    def __init__(self, hass, config_entry, weather_fetcher, location_key):
        """Initialize the wind speed sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._weather_fetcher = weather_fetcher

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
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            if weather_data_raw:
                weather_data = DataFormatter.format_weather_data(weather_data_raw)
                self._state = weather_data.get("wind_speed")
        except Exception as e:
            _LOGGER.error(f"Error updating wind speed: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()


class WindGustSensor(SensorEntity):
    """Sensor for wind gust speed."""

    should_poll = True

    def __init__(self, hass, config_entry, weather_fetcher, location_key):
        """Initialize the wind gust sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._weather_fetcher = weather_fetcher

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
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            if weather_data_raw:
                weather_data = DataFormatter.format_weather_data(weather_data_raw)
                self._state = weather_data.get("wind_gust", weather_data.get("wind_speed"))
        except Exception as e:
            _LOGGER.error(f"Error updating wind gust: {e}")
            self._state = None

    async def async_added_to_hass(self):
        await self.async_update()