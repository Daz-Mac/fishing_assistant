"""Weather data fetcher - Open-Meteo only (no Home Assistant weather entity fallback).

This version:
- Uses the injected Open-Meteo client when use_open_meteo is True.
- Does not attempt to read Home Assistant weather entities for current or forecast.
- Normalizes client returns (dicts, lists, objects) and converts units where explicit.
- Caches results and synthesizes forecasts from current if forecast is unavailable.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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

# Global cache to share weather/forecast data across sensors
_GLOBAL_CACHE: Dict[str, Dict[str, Any]] = {}


def _safe_float(v: Any, default: float) -> float:
    try:
        if v is None:
            return float(default)
        if isinstance(v, bool):
            return float(v)
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: Any, default: int) -> int:
    try:
        if v is None:
            return int(default)
        return int(float(v))
    except Exception:
        return int(default)


class WeatherFetcher:
    """
    Fetch current weather and forecast using an injected Open-Meteo client only.

    Parameters:
    - hass: Home Assistant core instance (kept for compatibility, not used for weather lookups).
    - latitude/longitude: location (rounded to 4 decimals used in cache keys).
    - use_open_meteo: if True and an open_meteo_client is provided, the fetcher will use it.
    - open_meteo_client: optional client object. The fetcher will attempt several common method names.
    """

    def __init__(
        self,
        hass,
        latitude: float,
        longitude: float,
        use_open_meteo: bool = True,
        open_meteo_client: Optional[Any] = None,
    ) -> None:
        self.hass = hass
        self.latitude = round(latitude, 4)
        self.longitude = round(longitude, 4)
        # weather_entity removed intentionally
        self.use_open_meteo = use_open_meteo
        self.open_meteo_client = open_meteo_client
        self._cache_key = f"{self.latitude}_{self.longitude}_{'om' if use_open_meteo else 'none'}"
        self._cache_duration = timedelta(minutes=30)

    # -----------------------
    # Public: current weather
    # -----------------------
    async def get_weather_data(self) -> Dict[str, Any]:
        """Get current weather data (normalized). Uses cache -> Open-Meteo -> defaults."""
        now = dt_util.now()

        # Use cached if present and fresh
        cache_entry = _GLOBAL_CACHE.get(self._cache_key)
        if cache_entry:
            cached_time = cache_entry.get("time")
            if isinstance(cached_time, datetime) and (now - cached_time) < self._cache_duration:
                _LOGGER.debug("Using cached weather data for %s", self._cache_key)
                return cache_entry["data"]

        # Only use Open-Meteo client (no HA weather entity fallback)
        if self.use_open_meteo and self.open_meteo_client:
            try:
                result = await self._call_open_meteo_current()
                if result:
                    _LOGGER.info("Fetched current weather from Open-Meteo client")
                    _GLOBAL_CACHE[self._cache_key] = {"data": result, "time": now}
                    return result
            except Exception as exc:
                _LOGGER.debug("Open-Meteo client current fetch failed: %s", exc, exc_info=True)

        # Fallback to defaults
        _LOGGER.warning("Unable to fetch weather data from Open-Meteo; using defaults")
        fallback = self._get_fallback_data()
        _GLOBAL_CACHE[self._cache_key] = {"data": fallback, "time": now}
        return fallback

    async def _call_open_meteo_current(self) -> Optional[Dict[str, Any]]:
        """
        Attempt to call the open_meteo_client to get current weather.

        Candidate behavior:
        - If client exposes a dedicated current method, call it.
        - If client returns hourly list, pick the entry closest to now.
        - Normalize known keys into the expected shape.
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
            "fetch_hourly_forecast",
            "get_hourly",
            "fetch_hourly",
        ]

        fn = None
        for name in candidate_methods:
            if hasattr(client, name):
                fn = getattr(client, name)
                break

        # If there's no candidate, try a generic 'fetch' or 'get' attribute
        if fn is None and hasattr(client, "fetch"):
            fn = getattr(client, "fetch")
        if fn is None:
            _LOGGER.debug("Open-Meteo client has no recognized current-weather method")
            return None

        try:
            # call method (sync or async)
            result = fn() if callable(fn) else None
            if inspect.isawaitable(result):
                result = await result

            if not result:
                return None

            # If result is a list of hourly entries -> pick nearest hour entry
            if isinstance(result, list):
                now = dt_util.now()
                best = None
                best_delta = None
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    t_raw = item.get("time") or item.get("datetime") or item.get("timestamp")
                    try:
                        t = dt_util.parse_datetime(str(t_raw)) if t_raw is not None else None
                    except Exception:
                        t = None
                    if t is None:
                        continue
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    delta = abs((t - now).total_seconds())
                    if best is None or delta < best_delta:
                        best = item
                        best_delta = delta
                if best:
                    mapped = self._map_to_current_shape(best)
                    return mapped
                return None

            # If result is a dict, try to map to expected shape
            if isinstance(result, dict):
                if "hourly" in result and isinstance(result.get("hourly"), dict):
                    hourly = result.get("hourly")
                    times = hourly.get("time") or []
                    if isinstance(times, list) and len(times) > 0:
                        idx = 0
                        try:
                            now = dt_util.now()
                            for i, t_raw in enumerate(times):
                                t = dt_util.parse_datetime(str(t_raw)) if t_raw is not None else None
                                if t and t.tzinfo is None:
                                    t = t.replace(tzinfo=timezone.utc)
                                if t and abs((t - now).total_seconds()) < 3600:
                                    idx = i
                                    break
                        except Exception:
                            idx = 0
                        candidate = {}
                        for k, arr in hourly.items():
                            if isinstance(arr, list) and len(arr) > idx:
                                candidate[k] = arr[idx]
                            else:
                                candidate[k] = None
                        mapped = self._map_to_current_shape(candidate)
                        return mapped
                mapped = self._map_to_current_shape(result)
                return mapped

            # If object with attributes, map to dict
            mapped = self._map_to_current_shape(result)
            return mapped
        except Exception as exc:
            _LOGGER.error("Error calling Open-Meteo current method: %s", exc, exc_info=True)
            return None

    def _map_to_current_shape(self, v: Any) -> Dict[str, Any]:
        """
        Map a returned value from a client into the expected current-weather dict.
        Accepts dict-like or objects with attributes and normalizes keys.
        Returned wind values are assumed to be km/h unless the source explicitly provides units.
        """
        if isinstance(v, dict):
            d = v
        else:
            # Try attribute access (fallback)
            d = {
                "temperature": getattr(v, "temperature", None),
                "temp": getattr(v, "temp", None),
                "wind_speed": getattr(v, "wind_speed", None),
                "wind_gust": getattr(v, "wind_gust", None),
                "cloud_cover": getattr(v, "cloud_cover", None),
                "precipitation_probability": getattr(v, "precipitation_probability", None),
                "pressure": getattr(v, "pressure", None),
                "units": getattr(v, "units", None),
                "wind_unit": getattr(v, "wind_unit", None),
            }

        # Helper to prefer several keys
        def pick(*keys, default=None):
            for k in keys:
                if k in d and d[k] is not None:
                    return d[k]
            return default

        temp = pick("temperature", "temp", "temperature_2m", "air_temperature", DEFAULT_WEATHER_VALUES["temperature"])
        wind = pick("wind_speed", "wind_kph", "wind_km_h", "wind", "windspeed", DEFAULT_WEATHER_VALUES["wind_speed"])
        wind_gust = pick("wind_gust", "gust", DEFAULT_WEATHER_VALUES["wind_gust"]) or wind
        cloud = pick("cloud_cover", "clouds", "clouds_percent", "cloud_coverage", DEFAULT_WEATHER_VALUES["cloud_cover"])
        precip = pick("precipitation_probability", "pop", "precipitation", "precip", DEFAULT_WEATHER_VALUES["precipitation_probability"])
        pressure = pick("pressure", "air_pressure", "pressure_msl", DEFAULT_WEATHER_VALUES["pressure"])

        # Units: prefer explicit wind_unit or units map; only convert if unit explicitly 'm/s'
        wind_unit = pick("wind_unit", "wind_speed_unit", None)
        try:
            temp_f = _safe_float(temp, DEFAULT_WEATHER_VALUES["temperature"])
            wind_f = _safe_float(wind, DEFAULT_WEATHER_VALUES["wind_speed"])
            wind_gust_f = _safe_float(wind_gust, DEFAULT_WEATHER_VALUES["wind_gust"])
            cloud_i = _safe_int(cloud, DEFAULT_WEATHER_VALUES["cloud_cover"])
            precip_i = _safe_int(precip, DEFAULT_WEATHER_VALUES["precipitation_probability"])
            pressure_f = _safe_float(pressure, DEFAULT_WEATHER_VALUES["pressure"])

            if wind_unit and str(wind_unit).strip().lower() in ("m/s", "mps"):
                # convert m/s -> km/h
                wind_f = wind_f * 3.6
                wind_gust_f = wind_gust_f * 3.6
        except Exception:
            _LOGGER.debug("Error coercing Open-Meteo client current values; falling back to defaults", exc_info=True)
            temp_f = DEFAULT_WEATHER_VALUES["temperature"]
            wind_f = DEFAULT_WEATHER_VALUES["wind_speed"]
            wind_gust_f = DEFAULT_WEATHER_VALUES["wind_gust"]
            cloud_i = DEFAULT_WEATHER_VALUES["cloud_cover"]
            precip_i = DEFAULT_WEATHER_VALUES["precipitation_probability"]
            pressure_f = DEFAULT_WEATHER_VALUES["pressure"]

        return {
            "temperature": temp_f,
            "wind_speed": wind_f,
            "wind_gust": wind_gust_f,
            "cloud_cover": cloud_i,
            "precipitation_probability": precip_i,
            "pressure": pressure_f,
        }

    # -----------------------
    # Public: forecast
    # -----------------------
    async def get_forecast(self, days: int = 7) -> Dict[str, Dict[str, Any]]:
        """
        Get weather forecast (date-keyed dict). Priority:
        - Open-Meteo client (if enabled)
        - Synthesize from current conditions
        """
        now = dt_util.now()
        forecast_cache_key = f"{self._cache_key}_forecast_{days}"

        cache_entry = _GLOBAL_CACHE.get(forecast_cache_key)
        if cache_entry:
            cached_time = cache_entry.get("time")
            if isinstance(cached_time, datetime) and (now - cached_time) < self._cache_duration:
                _LOGGER.debug("Using cached forecast data for %s", forecast_cache_key)
                return cache_entry["data"]

        # Only use Open-Meteo client (no HA forecast fallback)
        if self.use_open_meteo and self.open_meteo_client:
            try:
                result = await self._call_open_meteo_forecast(days)
                if result:
                    _LOGGER.info("Fetched forecast from Open-Meteo client")
                    _GLOBAL_CACHE[forecast_cache_key] = {"data": result, "time": now}
                    return result
            except Exception as exc:
                _LOGGER.debug("Open-Meteo client forecast fetch failed: %s", exc, exc_info=True)

        # Synthesize from current if forecast unavailable
        _LOGGER.warning("Unable to fetch forecast from Open-Meteo; synthesizing %s-day forecast from current.", days)
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
        Try to call forecast methods on the client. Accepts:
        - dict keyed by ISO dates
        - list of entries (hourly or daily)
        - Open-Meteo style dict with 'hourly' -> arrays (handled by api client usually)
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
            "fetch_hourly_forecast",
            "get_hourly",
            "fetch_hourly",
        ]

        fn = None
        for name in candidate_methods:
            if hasattr(client, name):
                fn = getattr(client, name)
                break
        if fn is None and hasattr(client, "fetch"):
            fn = getattr(client, "fetch")

        if fn is None:
            _LOGGER.debug("Open-Meteo client has no recognized forecast method")
            return None

        try:
            result = fn() if callable(fn) else None
            if inspect.isawaitable(result):
                result = await result

            if not result:
                return None

            # If dict keyed by dates
            if isinstance(result, dict):
                # If it's an Open-Meteo full response (contains 'hourly'), handle specially
                if "hourly" in result and isinstance(result.get("hourly"), dict):
                    hourly = result.get("hourly")
                    times = hourly.get("time") or []
                    items: List[Dict[str, Any]] = []
                    for idx, t in enumerate(times):
                        row = {"time": t}
                        for k, arr in hourly.items():
                            if k == "time":
                                continue
                            if isinstance(arr, list) and idx < len(arr):
                                row[k] = arr[idx]
                            else:
                                row[k] = None
                        items.append(row)
                    return self._normalize_hourly_list_to_daily(items, days)
                # If dict keyed by dates -> normalize entries
                normalized: Dict[str, Dict[str, Any]] = {}
                for key, val in sorted(result.items()):
                    date_key = str(key).split("T")[0]
                    if isinstance(val, dict):
                        temp = _safe_float(val.get("temperature") or val.get("temp") or val.get("temperature_2m"), DEFAULT_WEATHER_VALUES["temperature"])
                        wind = _safe_float(val.get("wind_speed") or val.get("wind") or val.get("wind_speed_10m"), DEFAULT_WEATHER_VALUES["wind_speed"])
                        gust = _safe_float(val.get("wind_gust") or val.get("gust") or wind, DEFAULT_WEATHER_VALUES["wind_gust"])
                        cloud = _safe_int(val.get("cloud_cover") or val.get("clouds"), DEFAULT_WEATHER_VALUES["cloud_cover"])
                        pop = _safe_int(val.get("precipitation_probability") or val.get("pop") or val.get("precipitation"), DEFAULT_WEATHER_VALUES["precipitation_probability"])
                        pressure = _safe_float(val.get("pressure") or val.get("pressure_msl"), DEFAULT_WEATHER_VALUES["pressure"])
                        normalized[date_key] = {
                            "temperature": temp,
                            "wind_speed": wind,
                            "wind_gust": gust,
                            "cloud_cover": cloud,
                            "precipitation_probability": pop,
                            "pressure": pressure,
                        }
                if normalized:
                    limited = dict(list(normalized.items())[:days])
                    return limited or None

            # If a list -> normalize entries (could be hourly or daily)
            if isinstance(result, list):
                first = next((it for it in result if isinstance(it, dict)), None)
                if first:
                    if "time" in first or "datetime" in first or "timestamp" in first:
                        return self._normalize_hourly_list_to_daily(result, days)
                    return self._normalize_forecast_list(result, days)

            # If object with 'daily' attribute
            if hasattr(result, "daily"):
                daily = getattr(result, "daily")
                if isinstance(daily, dict):
                    return await asyncio.get_event_loop().run_in_executor(None, lambda: self._call_sync_normalize_dict(daily, days))
                if isinstance(daily, list):
                    return self._normalize_hourly_list_to_daily(daily, days)

            _LOGGER.debug("Unrecognized forecast shape from Open-Meteo client: %s", type(result))
            return None
        except Exception as exc:
            _LOGGER.error("Error calling Open-Meteo forecast method: %s", exc, exc_info=True)
            return None

    def _call_sync_normalize_dict(self, d: Dict[str, Any], days: int) -> Optional[Dict[str, Dict[str, Any]]]:
        # helper invoked when we need to run normalization in executor for sync objects
        try:
            final: Dict[str, Dict[str, Any]] = {}
            for date_key in sorted(d.keys())[:days]:
                entry = d.get(date_key) or {}
                if not isinstance(entry, dict):
                    continue
                temp = _safe_float(entry.get("temperature") or entry.get("temp"), DEFAULT_WEATHER_VALUES["temperature"])
                wind = _safe_float(entry.get("wind_speed") or entry.get("wind"), DEFAULT_WEATHER_VALUES["wind_speed"])
                gust = _safe_float(entry.get("wind_gust") or entry.get("gust") or wind, DEFAULT_WEATHER_VALUES["wind_gust"])
                cloud = _safe_int(entry.get("cloud_cover") or entry.get("clouds"), DEFAULT_WEATHER_VALUES["cloud_cover"])
                pop = _safe_int(entry.get("precipitation_probability") or entry.get("pop") or entry.get("precipitation"), DEFAULT_WEATHER_VALUES["precipitation_probability"])
                pressure = _safe_float(entry.get("pressure") or entry.get("pressure_msl"), DEFAULT_WEATHER_VALUES["pressure"])
                final[date_key] = {
                    "temperature": temp,
                    "wind_speed": wind,
                    "wind_gust": gust,
                    "cloud_cover": cloud,
                    "precipitation_probability": pop,
                    "pressure": pressure,
                }
            return final or None
        except Exception:
            return None

    # -----------------------
    # Helpers: normalize various shapes
    # -----------------------
    def _normalize_forecast_list(self, lst: List[Any], days: int) -> Dict[str, Dict[str, Any]]:
        """Normalize list of dicts into date-keyed daily summaries (expects daily entries)."""
        final: Dict[str, Dict[str, Any]] = {}
        for item in lst:
            if not isinstance(item, dict):
                continue
            # pick a date key
            date_key = None
            for k in ("date", "time", "datetime", "day"):
                if k in item and item.get(k):
                    v = item.get(k)
                    try:
                        if isinstance(v, (int, float)):
                            dt = datetime.fromtimestamp(float(v), tz=timezone.utc)
                            date_key = dt.date().isoformat()
                        else:
                            s = str(v).split("T")[0]
                            # Validate basic YYYY-MM-DD
                            datetime.strptime(s, "%Y-%m-%d")
                            date_key = s
                    except Exception:
                        date_key = None
                    if date_key:
                        break
            if not date_key:
                continue

            temp = _safe_float(item.get("temperature") or item.get("temp"), DEFAULT_WEATHER_VALUES["temperature"])
            wind = _safe_float(item.get("wind_speed") or item.get("wind"), DEFAULT_WEATHER_VALUES["wind_speed"])
            gust = _safe_float(item.get("wind_gust") or item.get("gust") or wind, DEFAULT_WEATHER_VALUES["wind_gust"])
            cloud = _safe_int(item.get("cloud_cover") or item.get("clouds"), DEFAULT_WEATHER_VALUES["cloud_cover"])
            pop = _safe_int(item.get("precipitation_probability") or item.get("pop") or item.get("precipitation"), DEFAULT_WEATHER_VALUES["precipitation_probability"])
            pressure = _safe_float(item.get("pressure") or item.get("pressure_msl"), DEFAULT_WEATHER_VALUES["pressure"])

            final[date_key] = {
                "temperature": temp,
                "wind_speed": wind,
                "wind_gust": gust,
                "cloud_cover": cloud,
                "precipitation_probability": pop,
                "pressure": pressure,
            }
            if len(final) >= days:
                break
        return final

    def _normalize_hourly_list_to_daily(self, hourly_list: List[Any], days: int) -> Dict[str, Dict[str, Any]]:
        """
        Convert a list of hourly entries (dicts with a time field and numeric metrics) into daily summaries:
        - temperature: mean
        - wind_speed: mean
        - wind_gust: max
        - cloud_cover: mean (rounded)
        - precipitation_probability: max
        - pressure: mean
        """
        # Group by date
        per_date: Dict[str, Dict[str, Any]] = {}
        for entry in hourly_list:
            if not isinstance(entry, dict):
                continue
            # find time
            t_raw = entry.get("time") or entry.get("datetime") or entry.get("timestamp")
            if t_raw is None:
                continue
            try:
                t = dt_util.parse_datetime(str(t_raw)) if t_raw is not None else None
            except Exception:
                t = None
            if t is None:
                # try numeric epoch
                try:
                    tnum = float(t_raw)
                    if tnum > 1e12:
                        tnum = tnum / 1000.0
                    t = datetime.fromtimestamp(tnum, tz=timezone.utc)
                except Exception:
                    continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            date_key = t.date().isoformat()

            temp = _safe_float(entry.get("temperature") or entry.get("temp") or entry.get("temperature_2m"), DEFAULT_WEATHER_VALUES["temperature"])
            wind = _safe_float(entry.get("wind_speed") or entry.get("wind") or entry.get("wind_speed_10m"), DEFAULT_WEATHER_VALUES["wind_speed"])
            gust = _safe_float(entry.get("wind_gust") or entry.get("gust"), wind or DEFAULT_WEATHER_VALUES["wind_gust"])
            cloud = _safe_int(entry.get("cloud_cover") or entry.get("clouds") or entry.get("cloudcover"), DEFAULT_WEATHER_VALUES["cloud_cover"])
            pop = _safe_int(entry.get("precipitation_probability") or entry.get("pop") or entry.get("precipitation") or entry.get("precip"), DEFAULT_WEATHER_VALUES["precipitation_probability"])
            pressure = _safe_float(entry.get("pressure") or entry.get("pressure_msl"), DEFAULT_WEATHER_VALUES["pressure"])

            agg = per_date.get(date_key)
            if not agg:
                per_date[date_key] = {
                    "temperature_sum": temp,
                    "wind_speed_sum": wind,
                    "pressure_sum": pressure,
                    "cloud_sum": cloud,
                    "precip_max": pop,
                    "gust_max": gust,
                    "count": 1,
                }
            else:
                agg["temperature_sum"] += temp
                agg["wind_speed_sum"] += wind
                agg["pressure_sum"] += pressure
                agg["cloud_sum"] += cloud
                agg["precip_max"] = max(agg["precip_max"], pop)
                agg["gust_max"] = max(agg["gust_max"], gust)
                agg["count"] += 1

        if not per_date:
            return {}

        final: Dict[str, Dict[str, Any]] = {}
        for date_key in sorted(per_date.keys())[:days]:
            agg = per_date[date_key]
            cnt = agg.get("count", 1) or 1
            try:
                final[date_key] = {
                    "temperature": float(agg["temperature_sum"]) / cnt,
                    "wind_speed": float(agg["wind_speed_sum"]) / cnt,
                    "wind_gust": float(agg["gust_max"]),
                    "cloud_cover": int(round(float(agg["cloud_sum"]) / cnt)),
                    "precipitation_probability": int(round(float(agg["precip_max"]))),
                    "pressure": float(agg["pressure_sum"]) / cnt,
                }
            except Exception:
                final[date_key] = {
                    "temperature": DEFAULT_WEATHER_VALUES["temperature"],
                    "wind_speed": DEFAULT_WEATHER_VALUES["wind_speed"],
                    "wind_gust": DEFAULT_WEATHER_VALUES["wind_gust"],
                    "cloud_cover": DEFAULT_WEATHER_VALUES["cloud_cover"],
                    "precipitation_probability": DEFAULT_WEATHER_VALUES["precipitation_probability"],
                    "pressure": DEFAULT_WEATHER_VALUES["pressure"],
                }
        return final

    # -----------------------
    # Fallbacks
    # -----------------------
    def _get_fallback_data(self) -> Dict[str, Any]:
        """Return fallback weather data when client unavailable."""
        return DEFAULT_WEATHER_VALUES.copy()