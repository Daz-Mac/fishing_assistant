"""Weather data fetcher - supports robust Home Assistant weather entity parsing and optional Open-Meteo client.

This module:
- Reads current weather and forecast from a configured Home Assistant weather entity.
- Optionally uses a provided Open-Meteo client (dependency-injected) when configured.
- Normalizes forecast data into a simple dict keyed by ISO date strings with specific numeric fields.
- Caches results to reduce repeated calls.
- Provides defensive parsing to handle many legacy/variant forecast shapes.
"""

import logging
import inspect
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any, List, Union

import asyncio
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Default weather values used as fallbacks when data is missing
DEFAULT_WEATHER_VALUES = {
    "temperature": 15.0,
    "wind_speed": 10.0,  # km/h
    "wind_gust": 15.0,  # km/h
    "cloud_cover": 50,  # percentage
    "precipitation_probability": 0,  # percentage
    "pressure": 1013.0,  # hPa
}

# Global cache to share weather data across all sensors
_GLOBAL_CACHE: Dict[str, Dict[str, Any]] = {}


class WeatherFetcher:
    """
    Fetch weather/current and forecast data.

    Parameters:
    - hass: Home Assistant core instance (for reading states).
    - latitude, longitude: location (rounded to 4 decimals for consistent cache key).
    - weather_entity: optional HA weather entity id (string).
    - use_open_meteo: if True and an open_meteo_client is passed, the fetcher will try it first.
    - open_meteo_client: optional client object. This must implement an async method to return current and/or forecast.
      The fetcher will attempt to call commonly named methods (e.g. get_current, get_current_weather, fetch_current,
      get_forecast, fetch_forecast, get_daily_forecast). The client call is attempted in a defensive manner.
    """

    def __init__(
        self,
        hass,
        latitude: float,
        longitude: float,
        weather_entity: Optional[str] = None,
        use_open_meteo: bool = False,
        open_meteo_client: Optional[Any] = None,
    ):
        self.hass = hass
        self.latitude = round(latitude, 4)
        self.longitude = round(longitude, 4)
        self.weather_entity = weather_entity
        self.use_open_meteo = use_open_meteo
        self.open_meteo_client = open_meteo_client
        # Ensure weather_entity string-safe in cache key
        ent = str(weather_entity) if weather_entity is not None else "None"
        self._cache_key = f"{self.latitude}_{self.longitude}_{ent}_{'om' if use_open_meteo else 'ha'}"
        self._cache_duration = timedelta(minutes=30)  # Cache for 30 minutes

    async def get_weather_data(self) -> Dict[str, Any]:
        """Get current weather data (normalized)."""
        now = dt_util.now()
        # Check cache first
        cache_entry = _GLOBAL_CACHE.get(self._cache_key)
        if cache_entry:
            cached_time = cache_entry.get("time")
            if isinstance(cached_time, datetime) and (now - cached_time) < self._cache_duration:
                _LOGGER.debug("Using cached weather data for %s", self._cache_key)
                return cache_entry["data"]

        # Try Open-Meteo client if requested
        if self.use_open_meteo and self.open_meteo_client:
            try:
                result = await self._call_open_meteo_current()
                if result:
                    _LOGGER.info("Fetched current weather from Open-Meteo client")
                    _GLOBAL_CACHE[self._cache_key] = {"data": result, "time": now}
                    return result
            except Exception as exc:
                _LOGGER.debug("Open-Meteo client current fetch failed: %s", exc, exc_info=True)

        # Try to get data from HA weather entity
        if self.weather_entity:
            weather_data = await self._get_from_ha_entity()
            if weather_data:
                _GLOBAL_CACHE[self._cache_key] = {"data": weather_data, "time": now}
                _LOGGER.info("Fetched weather from HA entity: %s", self.weather_entity)
                return weather_data

        # Fallback to defaults
        _LOGGER.warning("Unable to fetch weather data from entity '%s' or Open-Meteo; using defaults", self.weather_entity)
        fallback = self._get_fallback_data()
        _GLOBAL_CACHE[self._cache_key] = {"data": fallback, "time": now}
        return fallback

    async def _call_open_meteo_current(self) -> Optional[Dict[str, Any]]:
        """
        Attempt to call the open_meteo_client to get current weather.
        Tries a set of common method names and expects a dict with the normalized keys:
        temperature, wind_speed (km/h), wind_gust (km/h), cloud_cover (percent),
        precipitation_probability (percent), pressure (hPa).
        If the client returns another shape, the method attempts minimal normalization.
        """
        client = self.open_meteo_client
        if client is None:
            return None

        candidate_methods = [
            "get_current",
            "get_current_weather",
            "fetch_current",
            "fetch_current_weather",
            "current",
            "get_now",
        ]
        fn = None
        for name in candidate_methods:
            if hasattr(client, name):
                fn = getattr(client, name)
                break

        if fn is None:
            _LOGGER.debug("Open-Meteo client has no recognized current-weather method")
            return None

        try:
            # call method, allow both sync and async callables
            if callable(fn):
                result = fn()
                if inspect.isawaitable(result):
                    result = await result
            else:
                _LOGGER.debug("Open-Meteo target attribute is not callable")
                return None

            if not result:
                return None

            # If client already returns normalized dict keyed fields, map them defensively
            mapped = self._map_to_current_shape(result)
            return mapped
        except Exception as exc:
            _LOGGER.error("Error calling Open-Meteo current method: %s", exc, exc_info=True)
            return None

    def _map_to_current_shape(self, v: Any) -> Dict[str, Any]:
        """
        Map a returned value from a client into the expected current-weather dict.
        Accepts dicts or objects with attributes.
        """
        if isinstance(v, dict):
            d = v
        else:
            # try attribute access for lightweight objects
            d = {
                "temperature": getattr(v, "temperature", None),
                "wind_speed": getattr(v, "wind_speed", None),
                "wind_gust": getattr(v, "wind_gust", None),
                "cloud_cover": getattr(v, "cloud_cover", None),
                "precipitation_probability": getattr(v, "precipitation_probability", None),
                "pressure": getattr(v, "pressure", None),
            }

        # helper to fetch number from many possible keys
        def get_number(keys, default):
            for k in keys:
                if k in d and d[k] is not None:
                    try:
                        return float(d[k])
                    except Exception:
                        continue
            return default

        mapped = {
            "temperature": get_number(["temperature", "temp", "t", "temperature_2m"], DEFAULT_WEATHER_VALUES["temperature"]),
            "wind_speed": get_number(["wind_speed", "wind_kph", "wind_km_h", "wind", "wind_speed_10m"], DEFAULT_WEATHER_VALUES["wind_speed"]),
            "wind_gust": get_number(["wind_gust", "wind_gust_kph", "gust"], DEFAULT_WEATHER_VALUES["wind_gust"]),
            "cloud_cover": int(get_number(["cloud_cover", "clouds", "clouds_percent", "cloud_coverage"], DEFAULT_WEATHER_VALUES["cloud_cover"])),
            "precipitation_probability": int(get_number(["precipitation_probability", "pop", "precipitation", "precip"], DEFAULT_WEATHER_VALUES["precipitation_probability"])),
            "pressure": get_number(["pressure", "air_pressure", "pressure_msl"], DEFAULT_WEATHER_VALUES["pressure"]),
        }
        return mapped

    async def _get_from_ha_entity(self) -> Optional[Dict[str, Any]]:
        """Get weather data from Home Assistant weather entity. Returns None on error."""
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return None

            attrs = state.attributes or {}

            # Common current value keys - fallbacks included
            temperature = attrs.get("temperature") or attrs.get("temp") or attrs.get("temperature_current") or attrs.get("state")
            wind_speed = attrs.get("wind_speed") or attrs.get("wind")
            wind_gust = attrs.get("wind_gust_speed") or attrs.get("wind_gust") or wind_speed
            pressure = attrs.get("pressure") or attrs.get("air_pressure")
            cloud_cover = attrs.get("cloud_coverage") or attrs.get("cloud_cover") or attrs.get("clouds")
            precip_prob = 0

            # If the forecast attribute exists and includes probabilities, try to pull the first entry's precipitation probability
            try:
                forecast_list = attrs.get("forecast", [])
                if isinstance(forecast_list, list) and len(forecast_list) > 0:
                    first = forecast_list[0] if isinstance(forecast_list[0], dict) else {}
                    precip_prob = first.get(
                        "precipitation_probability",
                        first.get("precipitation", first.get("precipitation_probability_percent", 0)),
                    ) or 0
            except Exception:
                precip_prob = 0

            # Some integrations may provide humidity instead of cloud cover — approximate
            if cloud_cover is None:
                humidity = attrs.get("humidity")
                if humidity is not None:
                    try:
                        cloud_cover = min(100, float(humidity) * 1.2)
                    except Exception:
                        cloud_cover = DEFAULT_WEATHER_VALUES["cloud_cover"]
                else:
                    cloud_cover = DEFAULT_WEATHER_VALUES["cloud_cover"]

            # Unit conversion if unit attribute present
            wind_unit = attrs.get("wind_speed_unit") or attrs.get("wind_unit")
            if wind_unit and isinstance(wind_speed, (int, float, str)):
                try:
                    if str(wind_unit) == "m/s":
                        wind_speed = float(wind_speed) * 3.6
                        if wind_gust is not None:
                            wind_gust = float(wind_gust) * 3.6
                    elif str(wind_unit) in ("mph", "mi/h"):
                        wind_speed = float(wind_speed) * 1.609344
                        if wind_gust is not None:
                            wind_gust = float(wind_gust) * 1.609344
                except Exception:
                    pass

            # Safe coercion to floats with defaults
            def safe_float(v, default):
                try:
                    return float(v) if v is not None else default
                except Exception:
                    return default

            weather_data: Dict[str, Any] = {
                "temperature": safe_float(temperature, DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": safe_float(wind_speed, DEFAULT_WEATHER_VALUES["wind_speed"]),
                "wind_gust": safe_float(wind_gust, DEFAULT_WEATHER_VALUES["wind_gust"]),
                "cloud_cover": int(safe_float(cloud_cover, DEFAULT_WEATHER_VALUES["cloud_cover"])),
                "precipitation_probability": int(safe_float(precip_prob, DEFAULT_WEATHER_VALUES["precipitation_probability"])),
                "pressure": safe_float(pressure, DEFAULT_WEATHER_VALUES["pressure"]),
            }

            _LOGGER.debug(
                "Parsed HA weather entity '%s': temp=%s°C, wind=%s km/h, gust=%s km/h, clouds=%s%%",
                self.weather_entity,
                weather_data["temperature"],
                weather_data["wind_speed"],
                weather_data["wind_gust"],
                weather_data["cloud_cover"],
            )

            return weather_data

        except Exception as exc:  # broad except to avoid integration crash
            _LOGGER.error("Error reading weather entity '%s': %s", self.weather_entity, exc, exc_info=True)
            return None

    async def get_forecast(self, days: int = 7) -> Dict[str, Dict[str, Any]]:
        """
        Get weather forecast (returns dict keyed by ISO date strings).

        Priority:
        - If use_open_meteo and open_meteo_client present -> attempt client forecast.
        - Else try the HA weather entity forecast attribute.
        - Otherwise synthesize forecast from current conditions.

        Result shape per date:
        {
            "temperature": float,
            "wind_speed": float,       # km/h
            "wind_gust": float,        # km/h
            "cloud_cover": int,        # percent
            "precipitation_probability": int,  # percent
            "pressure": float,         # hPa
        }
        """
        now = dt_util.now()
        forecast_cache_key = f"{self._cache_key}_forecast_{days}"

        # Check cache
        cache_entry = _GLOBAL_CACHE.get(forecast_cache_key)
        if cache_entry:
            cached_time = cache_entry.get("time")
            if isinstance(cached_time, datetime) and (now - cached_time) < self._cache_duration:
                _LOGGER.debug("Using cached forecast data for %s", forecast_cache_key)
                return cache_entry["data"]

        # Try Open-Meteo client if requested
        if self.use_open_meteo and self.open_meteo_client:
            try:
                result = await self._call_open_meteo_forecast(days)
                if result:
                    _LOGGER.info("Fetched forecast from Open-Meteo client")
                    _GLOBAL_CACHE[forecast_cache_key] = {"data": result, "time": now}
                    return result
            except Exception as exc:
                _LOGGER.debug("Open-Meteo client forecast fetch failed: %s", exc, exc_info=True)

        # Try HA entity forecast
        if self.weather_entity:
            forecast_data = await self._get_forecast_from_ha_entity(days)
            if forecast_data:
                _GLOBAL_CACHE[forecast_cache_key] = {"data": forecast_data, "time": now}
                _LOGGER.info("Fetched forecast from HA entity: %s", self.weather_entity)
                return forecast_data

        # Synthesize from current conditions
        _LOGGER.warning(
            "Unable to fetch forecast from entity '%s' or Open-Meteo; synthesizing %s-day forecast from current conditions.",
            self.weather_entity,
            days,
        )
        current = await self.get_weather_data()
        synthesized: Dict[str, Dict[str, Any]] = {}
        today = dt_util.now().date()
        for i in range(days):
            date_key = (today + timedelta(days=i)).isoformat()
            synthesized[date_key] = {
                "temperature": current.get("temperature", DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": current.get("wind_speed", DEFAULT_WEATHER_VALUES["wind_speed"]),
                "wind_gust": current.get("wind_gust", DEFAULT_WEATHER_VALUES["wind_gust"]),
                "cloud_cover": current.get("cloud_cover", DEFAULT_WEATHER_VALUES["cloud_cover"]),
                "precipitation_probability": current.get("precipitation_probability", DEFAULT_WEATHER_VALUES["precipitation_probability"]),
                "pressure": current.get("pressure", DEFAULT_WEATHER_VALUES["pressure"]),
            }

        _GLOBAL_CACHE[forecast_cache_key] = {"data": synthesized, "time": now}
        return synthesized

    async def _call_open_meteo_forecast(self, days: int) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Attempt to call the provided open_meteo_client to get a forecast.
        Will try several common method names and attempt to normalize returned shapes.
        Accepts:
        - dict keyed by ISO date strings -> returned as-is (but normalized)
        - list of entries with time/date and numeric fields -> will be normalized
        """
        client = self.open_meteo_client
        if client is None:
            return None

        candidate_methods = [
            "get_forecast",
            "get_daily_forecast",
            "fetch_forecast",
            "fetch_daily",
            "get_weather_forecast",
            "forecast",
        ]
        fn = None
        for name in candidate_methods:
            if hasattr(client, name):
                fn = getattr(client, name)
                break

        if fn is None:
            _LOGGER.debug("Open-Meteo client has no recognized forecast method")
            return None

        try:
            if callable(fn):
                result = fn()
                if inspect.isawaitable(result):
                    result = await result
            else:
                _LOGGER.debug("Open-Meteo target forecast attribute is not callable")
                return None

            if not result:
                return None

            # If client returned a dict keyed by dates, normalize values
            if isinstance(result, dict):
                return self._normalize_forecast_dict(result, days)

            # If client returned a list of entries, try to normalize it
            if isinstance(result, list):
                return self._normalize_forecast_list(result, days)

            # If client returned an object with attributes, try to detect typical keys
            if hasattr(result, "daily") and isinstance(result.daily, (dict, list)):
                if isinstance(result.daily, dict):
                    return self._normalize_forecast_dict(result.daily, days)
                return self._normalize_forecast_list(result.daily, days)

            _LOGGER.debug("Unrecognized forecast shape from Open-Meteo client: %s", type(result))
            return None
        except Exception as exc:
            _LOGGER.error("Error calling Open-Meteo forecast method: %s", exc, exc_info=True)
            return None

    def _normalize_forecast_dict(self, d: Dict[str, Any], days: int) -> Dict[str, Dict[str, Any]]:
        """
        Normalize a dict keyed by date strings to the standard shape.
        Values may already be normalized or may contain nested dicts/lists.
        """
        final: Dict[str, Dict[str, Any]] = {}
        # sort keys chronologically where possible
        sorted_dates = sorted(d.keys())
        for date_key in sorted_dates[:days]:
            entry = d.get(date_key) or {}
            if not isinstance(entry, dict):
                # If entry is a scalar, skip
                _LOGGER.debug("Skipping non-dict forecast entry for date %s: %s", date_key, type(entry))
                continue

            def pick(keys, default):
                for k in keys:
                    if k in entry and entry[k] is not None:
                        try:
                            return float(entry[k])
                        except Exception:
                            continue
                return default

            mapped = {
                "temperature": pick(["temperature", "temp", "t", "mean_temp", "avg_temp", "temperature_2m"], DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": pick(["wind_speed", "wind_kph", "wind_km_h", "wind", "wind_speed_10m"], DEFAULT_WEATHER_VALUES["wind_speed"]),
                "wind_gust": pick(["wind_gust", "gust", "wind_gust_kph"], DEFAULT_WEATHER_VALUES["wind_gust"]),
                "cloud_cover": int(pick(["cloud_cover", "clouds", "clouds_percent", "cloud_coverage"], DEFAULT_WEATHER_VALUES["cloud_cover"])),
                "precipitation_probability": int(pick(["precipitation_probability", "pop", "precipitation", "precip"], DEFAULT_WEATHER_VALUES["precipitation_probability"])),
                "pressure": pick(["pressure", "air_pressure", "pressure_msl"], DEFAULT_WEATHER_VALUES["pressure"]),
            }
            final[date_key] = mapped
        return final

    def _normalize_forecast_list(self, lst: List[Any], days: int) -> Dict[str, Dict[str, Any]]:
        """
        Normalize a list of forecast entries into a date-keyed dict.
        Tries to find time/date keys and numeric fields in a tolerant manner. Aggregates multiple
        entries per date by averaging temperature/wind/pressure and taking max for gust/precip.
        """
        forecast: Dict[str, Dict[str, Any]] = {}
        # common time keys
        time_keys = ("time", "datetime", "date", "date_time", "timestamp", "dt", "from", "to", "start", "datetime_utc")

        def try_parse_date(val: Any) -> Optional[str]:
            if val is None:
                return None
            try:
                # Handle epoch in seconds or milliseconds
                if isinstance(val, (int, float)):
                    v = float(val)
                    # Heuristic: timestamps > 1e12 are probably milliseconds
                    if v > 1e12:
                        v = v / 1000.0
                    dt = datetime.fromtimestamp(v, tz=timezone.utc)
                    return dt.date().isoformat()
                s = str(val).strip()
                if s.endswith("Z"):
                    s = s.replace("Z", "+00:00")
                # Try ISO parse
                try:
                    dt = datetime.fromisoformat(s)
                    # Ensure timezone-aware -> convert to date in UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    return dt.date().isoformat()
                except Exception:
                    # Try to coerce by splitting date part
                    date_only = s.split("T")[0]
                    # validate format YYYY-MM-DD
                    try:
                        datetime.strptime(date_only, "%Y-%m-%d")
                        return date_only
                    except Exception:
                        return None
            except Exception:
                return None

        def pick_number(dct: dict, keys, default):
            for k in keys:
                v = dct.get(k)
                if v is None:
                    continue
                try:
                    return float(v)
                except Exception:
                    continue
            return default

        for entry in lst:
            if not isinstance(entry, dict):
                continue

            # find date key
            dt_val = None
            for k in time_keys:
                if k in entry and entry[k]:
                    dt_val = entry[k]
                    break
            if dt_val is None:
                dt_val = entry.get("date") or entry.get("day")
            date_str = try_parse_date(dt_val)
            if not date_str:
                continue

            temperature = pick_number(entry, ["temperature", "temp", "t", "temp_mean", "temp_avg", "temperature_2m"], DEFAULT_WEATHER_VALUES["temperature"])
            wind_speed = pick_number(entry, ["wind_speed", "wind_kph", "wind", "wind_km_h", "wind_speed_10m"], DEFAULT_WEATHER_VALUES["wind_speed"])
            wind_gust = pick_number(entry, ["wind_gust", "gust", "wind_gust_kph"], wind_speed or DEFAULT_WEATHER_VALUES["wind_gust"])
            pressure = pick_number(entry, ["pressure", "air_pressure", "pressure_msl"], DEFAULT_WEATHER_VALUES["pressure"])
            cloud_cover = pick_number(entry, ["cloud_cover", "clouds", "clouds_percent", "cloud_coverage"], DEFAULT_WEATHER_VALUES["cloud_cover"])
            precip_prob = pick_number(entry, ["precipitation_probability", "pop", "precipitation", "precip"], DEFAULT_WEATHER_VALUES["precipitation_probability"])

            if date_str in forecast:
                existing = forecast[date_str]
                count = existing.get("_count", 1) + 1
                forecast[date_str] = {
                    "temperature": (existing["temperature"] * (count - 1) + temperature) / count,
                    "wind_speed": (existing["wind_speed"] * (count - 1) + wind_speed) / count,
                    "wind_gust": max(existing["wind_gust"], wind_gust),
                    "cloud_cover": (existing["cloud_cover"] * (count - 1) + cloud_cover) / count,
                    "precipitation_probability": max(existing["precipitation_probability"], precip_prob),
                    "pressure": (existing["pressure"] * (count - 1) + pressure) / count,
                    "_count": count,
                }
            else:
                forecast[date_str] = {
                    "temperature": temperature,
                    "wind_speed": wind_speed,
                    "wind_gust": wind_gust,
                    "cloud_cover": cloud_cover,
                    "precipitation_probability": precip_prob,
                    "pressure": pressure,
                    "_count": 1,
                }

        if not forecast:
            return {}

        # Build final trimmed result
        sorted_dates = sorted(forecast.keys())
        final: Dict[str, Dict[str, Any]] = {}
        for d_key in sorted_dates[:days]:
            entry = forecast[d_key]
            entry.pop("_count", None)
            try:
                final[d_key] = {
                    "temperature": float(entry.get("temperature", DEFAULT_WEATHER_VALUES["temperature"])),
                    "wind_speed": float(entry.get("wind_speed", DEFAULT_WEATHER_VALUES["wind_speed"])),
                    "wind_gust": float(entry.get("wind_gust", DEFAULT_WEATHER_VALUES["wind_gust"])),
                    "cloud_cover": int(float(entry.get("cloud_cover", DEFAULT_WEATHER_VALUES["cloud_cover"]))),
                    "precipitation_probability": int(float(entry.get("precipitation_probability", DEFAULT_WEATHER_VALUES["precipitation_probability"]))),
                    "pressure": float(entry.get("pressure", DEFAULT_WEATHER_VALUES["pressure"])),
                }
            except Exception:
                # On unexpected values, fallback to defaults for that date
                final[d_key] = DEFAULT_WEATHER_VALUES.copy()

        return final

    async def _get_forecast_from_ha_entity(self, days: int) -> Dict[str, Dict[str, Any]]:
        """Parse a forecast attribute from a HA weather entity into a simple daily dict."""
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return {}

            attrs = state.attributes or {}
            forecast_list = attrs.get("forecast") or attrs.get("forecasts") or []

            # If forecast is provided as a dict keyed by dates -> normalize dict
            if isinstance(forecast_list, dict):
                _LOGGER.debug("Forecast attribute is a dict for entity '%s', attempting dict normalizer", self.weather_entity)
                return self._normalize_forecast_dict(forecast_list, days)

            # If forecast is a list -> normalize list
            if isinstance(forecast_list, list) and len(forecast_list) > 0:
                normalized = self._normalize_forecast_list(forecast_list, days)
                if normalized:
                    return normalized
                # If list normalization failed but underlying items are dict keyed by dates,
                # try converting first element to dict (edge cases)
                _LOGGER.debug("List normalization yielded no usable entries for entity '%s'", self.weather_entity)
                return {}

            _LOGGER.debug("Weather entity '%s' has no usable forecast attribute", self.weather_entity)
            return {}
        except Exception as exc:
            _LOGGER.error("Error parsing forecast from entity '%s': %s", self.weather_entity, exc, exc_info=True)
            return {}

    def _get_fallback_data(self) -> Dict[str, Any]:
        """Return fallback weather data when entity is unavailable."""
        return DEFAULT_WEATHER_VALUES.copy()