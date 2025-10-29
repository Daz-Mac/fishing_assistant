"""Sensor platform for Fishing Assistant."""
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorEntity
from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from datetime import datetime, timezone, date, time
import logging
from typing import Any, Dict, Optional, List

from .const import (
    DOMAIN,
    CONF_MODE,
    MODE_FRESHWATER,
    MODE_OCEAN,
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
    """

    def __init__(self, client: OpenMeteoClient, latitude: float, longitude: float, include_marine: bool = False):
        self._client = client
        self._lat = latitude
        self._lon = longitude
        self._include_marine = include_marine

    async def get_forecast(self, days: int = 7) -> Optional[Dict[str, Dict]]:
        hourly = await self._client.fetch_hourly_forecast(
            self._lat, self._lon, include_marine=self._include_marine, forecast_days=days
        )
        if not hourly or not isinstance(hourly, list):
            return None

        per_day: Dict[str, Dict[str, Any]] = {}
        counts: Dict[str, int] = {}

        for item in hourly:
            t = item.get("time")
            try:
                dt = dt_util.parse_datetime(t) if t else None
            except Exception:
                dt = None
            if dt is None:
                # skip items we can't parse
                continue
            date_key = dt.astimezone(timezone.utc).date().isoformat()

            temp = item.get("temperature_2m")
            wind_ms = item.get("wind_speed_10m")  # m/s
            gust_ms = item.get("wind_gust_10m") or item.get("wind gust") or item.get("windgusts_10m")
            cloud = item.get("cloudcover") or item.get("cloud_cover")
            precip = item.get("precipitation") or item.get("rain") or item.get("precip")
            pressure = item.get("pressure_msl") or item.get("pressure")

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
                    _LOGGER.debug("Skipping non-numeric temperature value: %s", temp)

            if wind_ms is not None:
                try:
                    entry["wind_speed"] += float(wind_ms) * 3.6  # m/s -> km/h
                except Exception:
                    _LOGGER.debug("Skipping non-numeric wind value: %s", wind_ms)

            if gust_ms is not None:
                try:
                    gust_val = float(gust_ms) * 3.6
                    if gust_val > entry["wind_gust"]:
                        entry["wind_gust"] = gust_val
                except Exception:
                    _LOGGER.debug("Skipping non-numeric gust value: %s", gust_ms)

            if cloud is not None:
                try:
                    entry["cloud_cover"] += float(cloud)
                except Exception:
                    _LOGGER.debug("Skipping non-numeric cloud value: %s", cloud)

            if precip is not None:
                try:
                    if float(precip) > 0:
                        entry["precip_hours"] += 1
                except Exception:
                    _LOGGER.debug("Skipping non-numeric precip value: %s", precip)

            if pressure is not None:
                try:
                    entry["pressure"] += float(pressure)
                except Exception:
                    _LOGGER.debug("Skipping non-numeric pressure value: %s", pressure)

        final: Dict[str, Dict] = {}
        sorted_dates = sorted(per_day.keys())[:days]
        for d in sorted_dates:
            cnt = counts.get(d, 1) or 1
            e = per_day[d]

            avg_temp = e["temperature"] / cnt if cnt else None
            avg_wind = e["wind_speed"] / cnt if cnt else None
            gust = e["wind_gust"] or None
            if not gust and avg_wind is not None:
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

        return final or None

    async def get_current(self) -> Optional[Dict]:
        hourly = await self._client.fetch_hourly_forecast(
            self._lat, self._lon, include_marine=self._include_marine, forecast_days=1
        )
        if not hourly or not isinstance(hourly, list):
            return None

        now = datetime.now(timezone.utc)
        best = None
        best_delta = None
        for item in hourly:
            t = item.get("time")
            try:
                dt = dt_util.parse_datetime(t) if t else None
            except Exception:
                dt = None
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


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up fishing assistant sensors from a config entry."""
    data = config_entry.data
    mode = data.get(CONF_MODE, MODE_FRESHWATER)

    # Validate critical config keys early (fail loudly)
    for key in ("name", "latitude", "longitude"):
        if key not in data:
            _LOGGER.error("Config entry missing required key: %s", key)
            raise RuntimeError(f"Config entry missing required key: {key}")
    try:
        float(data["latitude"])
        float(data["longitude"])
    except Exception:
        _LOGGER.error(
            "Invalid latitude/longitude in config entry: %s / %s", data.get("latitude"), data.get("longitude")
        )
        raise RuntimeError("Invalid latitude/longitude in config entry")

    if mode == MODE_OCEAN:
        await _setup_ocean_sensors(hass, config_entry, async_add_entities)
    else:
        await _setup_freshwater_sensors(hass, config_entry, async_add_entities)


async def _setup_freshwater_sensors(hass, config_entry, async_add_entities):
    """Set up freshwater fishing sensors."""
    data = config_entry.data
    sensors = []

    # Validate required keys (fail loudly instead of defaulting)
    if "name" not in data or "latitude" not in data or "longitude" not in data:
        _LOGGER.error("Freshwater config missing name/latitude/longitude")
        raise RuntimeError("Freshwater config missing required keys")

    name = data["name"]
    lat = data["latitude"]
    lon = data["longitude"]
    fish_list = data.get("fish")
    body_type = data.get("body_type")
    tz = data.get("timezone")
    elevation = data.get("elevation")
    period_type = data.get(CONF_TIME_PERIODS, PERIOD_FULL_DAY)

    if not isinstance(fish_list, list) or not fish_list:
        _LOGGER.error("Freshwater config must include a non-empty 'fish' list")
        raise RuntimeError("Freshwater config missing fish list")

    use_open_meteo = data.get(CONF_USE_OPEN_METEO, True)
    if not use_open_meteo:
        _LOGGER.error("Only Open-Meteo is supported; config must enable use_open_meteo")
        raise RuntimeError("Open-Meteo must be enabled (no HA weather entity fallback)")

    session = async_get_clientsession(hass)
    client = OpenMeteoClient(session=session)
    open_meteo_adapter = OpenMeteoAdapter(client, lat, lon, include_marine=False)

    species_loader = SpeciesLoader(hass)
    await species_loader.async_load_profiles()

    # Ensure each selected fish exists in species profiles
    for fish in fish_list:
        if species_loader.get_species(fish) is None:
            _LOGGER.error("Selected freshwater species not found in species profiles: %s", fish)
            raise RuntimeError(f"Missing species profile for: {fish}")

    weather_fetcher = WeatherFetcher(
        hass, lat, lon, use_open_meteo=use_open_meteo, open_meteo_client=open_meteo_adapter
    )

    for fish in fish_list:
        sensors.append(
            FishScoreSensor(
                hass=hass,
                name=name,
                fish=fish,
                lat=lat,
                lon=lon,
                body_type=body_type,
                timezone=tz,
                elevation=elevation,
                period_type=period_type,
                weather_fetcher=weather_fetcher,
                species_loader=species_loader,
                config_entry_id=config_entry.entry_id,
            )
        )

    async_add_entities(sensors)


async def _setup_ocean_sensors(hass, config_entry, async_add_entities):
    """Set up ocean fishing sensors.

    NOTE: Only the main OceanFishingScoreSensor is created by default. All raw telemetry
    (tide, marine, weather) will be exposed as attributes on that sensor.
    """
    data = config_entry.data
    sensors = []

    # Validate keys
    if "name" not in data or "latitude" not in data or "longitude" not in data:
        _LOGGER.error("Ocean config missing name/latitude/longitude")
        raise RuntimeError("Ocean config missing required keys")

    name = data["name"]
    lat = data["latitude"]
    lon = data["longitude"]

    use_open_meteo = data.get(CONF_USE_OPEN_METEO, True)
    if not use_open_meteo:
        _LOGGER.error("Only Open-Meteo is supported; config must enable use_open_meteo")
        raise RuntimeError("Open-Meteo must be enabled (no HA weather entity fallback)")

    session = async_get_clientsession(hass)
    client = OpenMeteoClient(session=session)
    open_meteo_adapter = OpenMeteoAdapter(client, lat, lon, include_marine=data.get(CONF_MARINE_ENABLED, True))

    location_key = f"{name.lower().replace(' ', '_')}"

    tide_proxy = None
    marine_fetcher = None

    weather_fetcher = WeatherFetcher(
        hass, lat, lon, use_open_meteo=use_open_meteo, open_meteo_client=open_meteo_adapter
    )

    if data.get(CONF_TIDE_MODE) == TIDE_MODE_PROXY:
        tide_proxy = TideProxy(hass, lat, lon)

    if data.get(CONF_MARINE_ENABLED, True):
        marine_fetcher = MarineDataFetcher(hass, lat, lon)

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

    async_add_entities(sensors)


# ====#
# FRESHWATER SENSOR CLASS
# ====#

class FishScoreSensor(SensorEntity):
    """Sensor for freshwater fishing score."""

    should_poll = True

    def __init__(
        self,
        hass,
        name,
        fish,
        lat,
        lon,
        body_type,
        timezone,
        elevation,
        period_type,
        weather_fetcher,
        species_loader,
        config_entry_id,
    ):
        self.hass = hass
        self._last_update_hour: Optional[int] = None
        self._config_entry_id = config_entry_id
        self._device_identifier = f"{name}_{lat}_{lon}"
        self._name = f"{name.lower().replace(' ', '_')}_{fish}_score"
        self._friendly_name = f"{name} ({fish.title()}) Fishing Score"
        self._state = None
        self._species_loader = species_loader
        self._weather_fetcher = weather_fetcher

        species_profile = species_loader.get_species(fish)
        species_profiles = {fish: species_profile} if species_profile else {}

        self._scorer = FreshwaterFishingScorer(latitude=lat, longitude=lon, species=[fish], species_profiles=species_profiles)

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
            "via_device": None,
        }

    async def async_update(self):
        """Fetch the current score and forecast."""
        now = dt_util.now()
        update_hours = [0, 6, 12, 18]

        if self._last_update_hour is not None and now.hour not in update_hours:
            _LOGGER.debug("Skipping update for %s; not in update hours: %s", self._name, now.hour)
            return

        if self._last_update_hour == now.hour:
            _LOGGER.debug("Already updated this hour for %s", self._name)
            return

        try:
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            astro_data = await self._get_astro_data()

            result = self._scorer.calculate_score(weather_data=weather_data_raw, astro_data=astro_data, current_time=now)

            if not isinstance(result, dict):
                _LOGGER.error("Scorer returned unexpected result type for %s: %s", self._name, type(result))
                raise RuntimeError("Scorer returned unexpected result type")

            self._state = result.get("score")

            # Populate new inspection/raw attributes only (no legacy keys)
            self._attrs.update(
                {
                    "component_scores": result.get("component_scores", {}),
                    "score_breakdown": result.get("score_breakdown", {}) or result.get("component_scores", {}),
                    "last_updated": now.isoformat(),
                    "weather_snapshot_raw": weather_data_raw or {},
                    "astro_snapshot_raw": astro_data or {},
                }
            )

            # Forecast: let errors surface
            forecast_raw = await self._weather_fetcher.get_forecast(days=7)
            if forecast_raw and isinstance(forecast_raw, dict):
                forecast_list = []
                for date_str, data in forecast_raw.items():
                    forecast_date = None
                    try:
                        forecast_date = dt_util.parse_datetime(date_str)
                    except Exception:
                        forecast_date = None

                    if forecast_date is None:
                        try:
                            d = date.fromisoformat(date_str)
                            forecast_date = datetime.combine(d, time.min, tzinfo=timezone.utc)
                        except Exception:
                            _LOGGER.debug("Unable to parse forecast date key: %s", date_str)
                            continue

                    if forecast_date.tzinfo is None:
                        forecast_date = forecast_date.replace(tzinfo=timezone.utc)

                    data = dict(data) if isinstance(data, dict) else {}
                    data["datetime"] = forecast_date
                    forecast_list.append(data)

                if forecast_list:
                    forecast_scores = await self._scorer.calculate_forecast(weather_forecast=forecast_list)
                    # Provide only the new non-legacy key
                    self._attrs["forecast_breakdown"] = forecast_scores
                    # Keep a compact per-step summary
                    self._attrs["score_breakdown"] = result.get("component_scores", {})

            self._last_update_hour = now.hour

            _LOGGER.debug("Updated %s: score=%s, component_scores=%s", self._name, self._state, self._attrs.get("component_scores"))

        except Exception:
            _LOGGER.exception("Error updating freshwater sensor %s - bubbling up", self._name)
            raise

    async def _get_astro_data(self):
        """Get astronomical data from Home Assistant (sun + moon)."""
        sun_state = self.hass.states.get("sun.sun")
        moon_state = self.hass.states.get("sensor.moon")

        astro: Dict[str, Any] = {}

        if sun_state:
            sunrise_str = sun_state.attributes.get("next_rising")
            sunset_str = sun_state.attributes.get("next_setting")

            try:
                if sunrise_str:
                    astro["sunrise"] = dt_util.parse_datetime(sunrise_str)
                if sunset_str:
                    astro["sunset"] = dt_util.parse_datetime(sunset_str)
            except Exception:
                _LOGGER.debug("Failed to parse sun times: %s / %s", sunrise_str, sunset_str, exc_info=True)

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


# ====#
# OCEAN MODE: Only the aggregated OceanFishingScoreSensor remains
# ====#

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

        species_loader = SpeciesLoader(hass)

        self._scorer = OceanFishingScorer(
            latitude=lat, longitude=lon, species=[species_id], species_profiles={}, hass=hass, config=data
        )

        self._device_identifier = f"{name}_{lat}_{lon}_ocean"
        self._name = f"{name.lower().replace(' ', '_')}_ocean_fishing_score"
        self._friendly_name = f"{name} Ocean Fishing Score"
        self._state = None
        self._last_update_hour: Optional[int] = None

        # Minimal attributes initially; full canonical attributes will be produced on update
        self._attrs: Dict[str, Any] = {
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
        try:
            val = float(self._state)
            if val >= 8:
                return "mdi:fish"
            if val >= 6:
                return "mdi:fish-off"
            return "mdi:waves"
        except Exception:
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
        """Update the fishing score and package all telemetry into the main sensor attributes."""
        now = dt_util.now()
        update_hours = [0, 6, 12, 18]

        if self._last_update_hour is not None and now.hour not in update_hours:
            _LOGGER.debug("Skipping update for ocean sensor %s; not in update hours: %s", self._name, now.hour)
            return

        if self._last_update_hour == now.hour:
            _LOGGER.debug("Already updated this hour for ocean sensor %s", self._name)
            return

        try:
            # Gather raw data (WeatherFetcher now raises on failure)
            weather_data_raw = await self._weather_fetcher.get_weather_data()
            tide_data_raw = await self._tide_proxy.get_tide_data() if self._tide_proxy else None
            marine_data_raw = await self._marine_fetcher.get_marine_data() if self._marine_fetcher else None
            astro_data = await self._get_astro_data()

            # Always populate raw snapshot attributes for inspectability (even on errors)
            self._attrs.update(
                {
                    "weather_snapshot_raw": weather_data_raw or {},
                    "astro_snapshot_raw": astro_data or {},
                    "marine_snapshot_raw": marine_data_raw or {},
                    "tide_snapshot_raw": tide_data_raw or {},
                }
            )

            # If configuration requires marine data, and it's missing or empty, set an error attribute and stop.
            if self._config_entry.data.get(CONF_MARINE_ENABLED, True):
                if not marine_data_raw or not isinstance(marine_data_raw, dict) or not marine_data_raw.get("current"):
                    err_msg = "Missing required marine data — ocean scoring requires marine snapshots (current + forecast)."
                    _LOGGER.error(err_msg)
                    # Populate attributes with clear error state so the user can debug in HA without silent zeros
                    self._state = None
                    self._attrs.update(
                        {
                            "status": "error",
                            "error_message": err_msg,
                            "forecast_breakdown": [],
                            "score_breakdown": {},
                        }
                    )
                    return

            # If configuration uses tide proxy, require tide data presence
            if self._config_entry.data.get(CONF_TIDE_MODE) == TIDE_MODE_PROXY:
                if not tide_data_raw or not isinstance(tide_data_raw, dict) or tide_data_raw.get("state") in (None, "unknown"):
                    err_msg = "Missing required tide data — tide proxy mode configured but tide data unavailable."
                    _LOGGER.error(err_msg)
                    self._state = None
                    self._attrs.update(
                        {
                            "status": "error",
                            "error_message": err_msg,
                            "forecast_breakdown": [],
                            "score_breakdown": {},
                        }
                    )
                    return

            # Calculate score using scorer
            result = self._scorer.calculate_score(
                weather_data=weather_data_raw, astro_data=astro_data, tide_data=tide_data_raw, marine_data=marine_data_raw, current_time=now
            )

            if not isinstance(result, dict):
                _LOGGER.error(
                    "Scorer returned unexpected result type for ocean sensor %s: %s",
                    self._name,
                    type(result),
                )
                raise RuntimeError("Scorer returned unexpected result type")

            # Update numeric state
            self._state = result.get("score")

            # Forecast (may raise) — let failures bubble up
            forecast_raw = await self._weather_fetcher.get_forecast(days=7)

            # Convert tide/marine forecast shapes into lists usable by scorer.calculate_forecast
            def _to_list_forecast(f_obj: Any) -> List[Dict[str, Any]]:
                """Normalize various forecast shapes into a list of dict items with a 'datetime' key."""
                out: List[Dict[str, Any]] = []
                if not f_obj:
                    return out
                # Already a list
                if isinstance(f_obj, list):
                    return f_obj
                # If dict with 'items' list
                if isinstance(f_obj, dict):
                    if isinstance(f_obj.get("items"), list):
                        return f_obj.get("items")
                    # If dict keyed by date (YYYY-MM-DD) -> values, convert to list with datetime
                    for k, v in f_obj.items():
                        # Skip meta keys
                        if k in ("source", "last_updated"):
                            continue
                        entry: Dict[str, Any] = {}
                        if isinstance(v, dict):
                            entry.update(v)
                        # Try parse key as date/datetime
                        dt_val = None
                        try:
                            dt_val = dt_util.parse_datetime(str(k))
                        except Exception:
                            dt_val = None
                        if dt_val is None:
                            try:
                                d = date.fromisoformat(str(k))
                                dt_val = datetime.combine(d, time.min, tzinfo=timezone.utc)
                            except Exception:
                                dt_val = None
                        entry["datetime"] = dt_val if dt_val is not None else k
                        out.append(entry)
                    return out
                # Unknown shape -> leave empty
                return out

            tide_list = _to_list_forecast((tide_data_raw or {}).get("forecast") if isinstance(tide_data_raw, dict) else None)
            marine_list = _to_list_forecast((marine_data_raw or {}).get("forecast") if isinstance(marine_data_raw, dict) else None)

            forecast_breakdown: List[Dict[str, Any]] = []
            marine_forecast_raw: List[Dict[str, Any]] = []
            tide_forecast_raw: List[Dict[str, Any]] = []

            if forecast_raw and isinstance(forecast_raw, dict):
                # Build weather forecast list similar to freshwater flow (date -> data)
                forecast_list = []
                for date_str, data in forecast_raw.items():
                    forecast_date = None
                    try:
                        forecast_date = dt_util.parse_datetime(date_str)
                    except Exception:
                        forecast_date = None

                    if forecast_date is None:
                        try:
                            d = date.fromisoformat(date_str)
                            forecast_date = datetime.combine(d, time.min, tzinfo=timezone.utc)
                        except Exception:
                            _LOGGER.debug("Unable to parse forecast date key: %s", date_str)
                            continue

                    if forecast_date.tzinfo is None:
                        forecast_date = forecast_date.replace(tzinfo=timezone.utc)

                    data = dict(data) if isinstance(data, dict) else {}
                    data["datetime"] = forecast_date
                    forecast_list.append(data)

                if forecast_list:
                    # Pass tide/marine lists so scorer can incorporate them into per-step scoring
                    forecast_breakdown = await self._scorer.calculate_forecast(
                        weather_forecast=forecast_list, tide_forecast=tide_list, marine_forecast=marine_list
                    )

                    marine_forecast_raw = marine_list or []
                    tide_forecast_raw = tide_list or []

                    # Attach the new non-legacy keys
                    self._attrs["forecast_breakdown"] = forecast_breakdown
                    self._attrs["marine_forecast_raw"] = marine_forecast_raw
                    self._attrs["tide_forecast_raw"] = tide_forecast_raw
                    self._attrs["score_breakdown"] = result.get("component_scores", {})

            # Use DataFormatter to produce canonical sensor attributes that include
            # formatted weather, marine, tide, astro and forecast structures.
            formatted_attrs = DataFormatter.format_sensor_attributes(
                score=self._state,
                conditions=result.get("conditions_summary") or result.get("conditions") or "",
                component_scores=result.get("component_scores") or {},
                weather=weather_data_raw or {},
                astro=astro_data or {},
                mode="ocean",
                species=[self._scorer.species_profile.get("id")]
                if getattr(self._scorer, "species_profile", None)
                else [self._attrs.get("species_focus")],
                location=self._attrs.get("location"),
                forecast=forecast_raw or {},
                marine=marine_data_raw or {},
                tide=tide_data_raw or {},
            )

            # Remove legacy keys from formatted_attrs if present
            for legacy_key in ("forecast", "forecast_raw", "breakdown", "rating"):
                if legacy_key in formatted_attrs:
                    formatted_attrs.pop(legacy_key, None)

            # Merge results: canonical formatted_attrs + our new inspection keys
            merged = {**formatted_attrs}
            merged.update(
                {
                    "forecast_breakdown": self._attrs.get("forecast_breakdown", []),
                    "marine_snapshot_raw": self._attrs.get("marine_snapshot_raw", {}),
                    "marine_forecast_raw": self._attrs.get("marine_forecast_raw", []),
                    "tide_snapshot_raw": self._attrs.get("tide_snapshot_raw", {}),
                    "tide_forecast_raw": self._attrs.get("tide_forecast_raw", []),
                    "weather_snapshot_raw": self._attrs.get("weather_snapshot_raw", {}),
                    "astro_snapshot_raw": self._attrs.get("astro_snapshot_raw", {}),
                    "score_breakdown": self._attrs.get("score_breakdown", {}),
                }
            )

            self._attrs = merged

            # Add a compact 'safety' summary (scorer.check_safety returns status + reasons)
            try:
                weather_formatted = DataFormatter.format_weather_data(weather_data_raw)
                marine_formatted = DataFormatter.format_marine_data(marine_data_raw) if marine_data_raw else {"current": {}, "forecast": {}}
                safety_status, safety_reasons = self._scorer.check_safety(weather_formatted, marine_formatted)
                self._attrs["safety"] = {"status": safety_status, "reasons": safety_reasons}
            except Exception:
                _LOGGER.debug("Safety check failed while updating ocean sensor attributes", exc_info=True)

            self._last_update_hour = now.hour

            _LOGGER.debug("Updated %s: score=%s, component_scores=%s", self._name, self._state, self._attrs.get("score_breakdown"))

        except Exception:
            _LOGGER.exception("Error updating ocean fishing score for %s - bubbling up", self._name)
            raise

    async def _get_astro_data(self):
        """Get astronomical data from Home Assistant."""
        sun_state = self.hass.states.get("sun.sun")
        moon_state = self.hass.states.get("sensor.moon")

        astro: Dict[str, Any] = {}

        if sun_state:
            sunrise_str = sun_state.attributes.get("next_rising")
            sunset_str = sun_state.attributes.get("next_setting")

            try:
                if sunrise_str:
                    astro["sunrise"] = dt_util.parse_datetime(sunrise_str)
                if sunset_str:
                    astro["sunset"] = dt_util.parse_datetime(sunset_str)
            except Exception:
                _LOGGER.debug("Failed to parse sun times for ocean sensor: %s / %s", sunrise_str, sunset_str, exc_info=True)

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
        try:
            await self._scorer.async_initialize()
        except Exception:
            # Log but continue; scorer init should not block entity creation
            _LOGGER.debug("Scorer async_initialize failed or not present for %s", self._name, exc_info=True)

        # Update species_focus if the scorer loaded a profile
        try:
            if getattr(self._scorer, "species_profile", None):
                self._attrs["species_focus"] = self._scorer.species_profile.get("name", self._attrs.get("species_focus"))
        except Exception:
            _LOGGER.debug("Error reading species_profile for %s", self._name, exc_info=True)

        # Run initial update - allow errors to surface
        await self.async_update()