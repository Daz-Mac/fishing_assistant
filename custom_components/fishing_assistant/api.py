"""Open-Meteo API client and normalizer for Fishing Assistant.

Provides:
- OpenMeteoClient: async fetch of hourly weather + optional marine data
- normalize_hourly_merged: converts Open-Meteo `hourly` arrays into a list of
  timestamped dicts following the integration's canonical forecast contract.

Normalization contract (per hourly item):
{
    "time": "2025-10-26T14:00:00Z",
    "temperature_2m": 13.7,
    "wind_speed_10m": 3.2,
    "cloudcover": 75,
    "precipitation": 0.0,
    "pressure_msl": 1012.3,
    "wave_height": 0.8,
    "wave_period": 5.6,
    "sea_surface_temperature": 12.1,
    ...
}

The client is defensive: logs raw response keys, checks lengths, coerces numeric-like
strings to numbers, normalizes times to UTC ISO strings ending with 'Z', and fills
missing values with None.
"""

import logging
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Iterable

import aiohttp
from homeassistant.util import dt as dt_util

from .const import OPEN_METEO_URL, OPEN_METEO_MARINE_URL

_LOGGER = logging.getLogger(__name__)


class OpenMeteoClient:
    """Client to fetch and normalize Open-Meteo data."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        # If a session is provided (e.g. Home Assistant's), we won't close it.
        self._session = session

    async def fetch_hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        include_marine: bool = False,
        forecast_days: int = 7,
        hourly_vars: Optional[Iterable[str]] = None,
        marine_vars: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch hourly forecast (weather + optional marine) and return normalized list.

        Returns a list of dicts (one per hour) normalized per contract.
        """
        # Default hourly variables useful for scoring
        if hourly_vars is None:
            hourly_vars = [
                "temperature_2m",
                "cloudcover",
                "precipitation",
                "wind_speed_10m",
                "pressure_msl",
            ]
        if marine_vars is None:
            marine_vars = [
                "wave_height",
                "wave_period",
                "sea_surface_temperature",
            ]

        _LOGGER.debug(
            "Fetching Open-Meteo weather: lat=%s lon=%s vars=%s days=%s",
            latitude,
            longitude,
            ",".join(hourly_vars),
            forecast_days,
        )

        weather_data = await self._fetch_open_meteo(
            OPEN_METEO_URL,
            latitude,
            longitude,
            list(hourly_vars),
            forecast_days,
        )

        marine_data = None
        if include_marine:
            try:
                _LOGGER.debug("Fetching Open-Meteo marine data")
                marine_data = await self._fetch_open_meteo(
                    OPEN_METEO_MARINE_URL,
                    latitude,
                    longitude,
                    list(marine_vars),
                    forecast_days,
                    is_marine=True,
                )
            except Exception as exc:  # keep defensive: don't break whole flow due to marine failure
                _LOGGER.debug("Marine endpoint fetch failed: %s; will attempt to merge whatever is available", exc)

        # Normalize / merge into hourly list
        normalized = normalize_hourly_merged(weather_data, marine_data)

        # Debug logs
        try:
            if isinstance(weather_data, dict):
                _LOGGER.debug("Open-Meteo raw keys (weather): %s", list(weather_data.keys()))
            else:
                _LOGGER.debug("Open-Meteo weather response type: %s", type(weather_data))

            if marine_data:
                if isinstance(marine_data, dict):
                    _LOGGER.debug("Open-Meteo raw keys (marine): %s", list(marine_data.keys()))
                else:
                    _LOGGER.debug("Open-Meteo marine response type: %s", type(marine_data))

            _LOGGER.debug("Normalized forecast count=%d; first_items=%s", len(normalized), normalized[:3])
        except Exception:
            # Never fail because of logging
            pass

        return normalized

    async def _fetch_open_meteo(
        self,
        base_url: str,
        latitude: float,
        longitude: float,
        hourly: List[str],
        forecast_days: int,
        is_marine: bool = False,
    ) -> Dict[str, Any]:
        """Perform the HTTP GET to Open-Meteo and return parsed JSON.

        Raises on non-200 responses.

        NOTE: This function is defensive and logs request/response metadata to help debugging.
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(hourly),
            "timezone": "UTC",
            "forecast_days": forecast_days,
        }

        session = self._session or aiohttp.ClientSession()
        close_session = self._session is None

        _LOGGER.debug("Open-Meteo request to %s params=%s", base_url, params)
        try:
            async with session.get(base_url, params=params) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.debug("Open-Meteo non-200 response: %s body=%s", resp.status, text)
                    raise Exception(f"Open-Meteo returned status {resp.status}: {text}")
                try:
                    json_data = await resp.json()
                    _LOGGER.debug("Open-Meteo response keys: %s", list(json_data.keys()) if isinstance(json_data, dict) else type(json_data))
                    return json_data
                except Exception as exc:
                    _LOGGER.debug("Failed to parse Open-Meteo JSON: %s; raw body: %s", exc, text)
                    raise
        finally:
            if close_session:
                # Close only the client we created
                try:
                    await session.close()
                except Exception:
                    pass


# -----------------------------
# Normalization helpers
# -----------------------------


def _to_utc_iso(dt_val: Any) -> Optional[str]:
    """Parse a datetime-like value (string or datetime) and return a UTC ISO string with Z.

    Accepts Home Assistant's dt_util.parse_datetime inputs, naive datetimes (assumed UTC),
    or ISO strings. Returns None on failure.
    """
    if dt_val is None:
        return None
    try:
        if isinstance(dt_val, str):
            parsed = dt_util.parse_datetime(dt_val)
        elif isinstance(dt_val, datetime):
            parsed = dt_val
        else:
            # Try converting other types to string and parse
            parsed = dt_util.parse_datetime(str(dt_val))

        if parsed is None:
            return None

        # Ensure timezone-aware in UTC
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed_utc = parsed.astimezone(timezone.utc)
        # Use Z suffix for compactness
        return parsed_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _coerce_numeric(value: Any) -> Any:
    """Try to coerce numeric-like strings to numbers; leave booleans as-is.

    Returns float for decimal-like values, int if integer-like, or original value on failure.
    """
    if value is None:
        return None
    # Preserve booleans
    if isinstance(value, bool):
        return value
    # Already numeric
    if isinstance(value, (int, float)):
        # Normalize NaN to None
        try:
            if isinstance(value, float) and math.isnan(value):
                return None
        except Exception:
            pass
        return value
    # Strings that represent numbers
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            # Try int first
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                return int(s)
            # Try float
            f = float(s)
            if math.isnan(f):
                return None
            # If float is actually integer (e.g. "2.0"), return int
            if float(int(f)) == f:
                return int(f)
            return f
        except Exception:
            return value
    # fallback: return as-is
    return value


def normalize_hourly_response(raw: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Return the 'hourly' dict from Open-Meteo response or an empty dict.

    The function returns a dict mapping variable->list, with special handling for 'time'.
    Ensures that all returned values are lists (or missing).
    """
    if not raw or not isinstance(raw, dict):
        return {}
    hourly = raw.get("hourly") or {}

    result: Dict[str, List[Any]] = {}
    for k, v in hourly.items():
        # Only keep serializable list-like arrays or scalar values (converted to single-element list)
        if isinstance(v, list):
            result[k] = v
        else:
            # Some variants may include scalars (rare) - convert to single-element list for consistent indexing
            result[k] = [v]
    return result


def normalize_hourly_merged(weather_raw: Optional[Dict[str, Any]], marine_raw: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Merge weather and marine hourly arrays into a normalized list of dicts.

    Uses weather_raw.hourly.time as the canonical timeline when available. If not,
    falls back to marine times or best-effort.

    Returns a list of dicts, where numeric-like strings are coerced to numbers,
    empty values and NaNs become None, and times are normalized to UTC ISO strings.
    """
    weather_hourly = normalize_hourly_response(weather_raw)
    marine_hourly = normalize_hourly_response(marine_raw)

    # Determine canonical times (lists). These are lists of original time values.
    times = weather_hourly.get("time") or marine_hourly.get("time") or []

    # Normalize times to UTC ISO strings. Keep same length as times.
    times_iso: List[Optional[str]] = [_to_utc_iso(t) for t in times]

    # If times present, expected length is len(times_iso). Otherwise zero.
    expected_len = len(times_iso)

    # Collect union of variable keys (exclude 'time')
    keys = set(weather_hourly.keys()) | set(marine_hourly.keys())
    keys.discard("time")

    # If times list is empty but we have some scalar fields, return a single snapshot
    if expected_len == 0:
        item: Dict[str, Any] = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for k in keys:
            vals = weather_hourly.get(k) or marine_hourly.get(k)
            # vals should be a list per normalize_hourly_response; pick first meaningful element
            chosen = None
            if isinstance(vals, list) and len(vals) > 0:
                chosen = vals[0]
            else:
                # empty list or None -> None
                chosen = None
            # Coerce numeric strings / normalize NaN/empty
            chosen = _coerce_numeric(chosen)
            item[k] = chosen
        return [item]

    # Pre-check lengths and log mismatches for debugging
    for src_name, arr in (("weather", weather_hourly), ("marine", marine_hourly)):
        for k, v in arr.items():
            if k == "time":
                continue
            if isinstance(v, list) and len(v) != expected_len:
                _LOGGER.debug(
                    "Variable '%s' from %s has length %d but expected %d (times length).",
                    k,
                    src_name,
                    len(v),
                    expected_len,
                )

    # Build final list of items
    normalized: List[Dict[str, Any]] = []
    for idx in range(expected_len):
        t_iso = times_iso[idx] if idx < len(times_iso) else None
        # If a particular time couldn't be parsed, log once (debug)
        if t_iso is None:
            _LOGGER.debug("Unparseable time at index %d (raw=%s)", idx, times[idx] if idx < len(times) else None)

        item: Dict[str, Any] = {"time": t_iso}
        for k in keys:
            val = None

            # Prefer weather arrays, fallback to marine arrays
            src_arr = weather_hourly.get(k)
            if isinstance(src_arr, list):
                if idx < len(src_arr):
                    val = src_arr[idx]
                else:
                    val = None
            else:
                # Not expected, but handle gracefully by broadcasting scalar
                val = src_arr if src_arr is not None else None

            if val is None:
                src_arr2 = marine_hourly.get(k)
                if isinstance(src_arr2, list):
                    if idx < len(src_arr2):
                        val = src_arr2[idx]
                else:
                    if src_arr2 is not None:
                        val = src_arr2

            # Normalize sentinel values: empty string, "nan" string, float('nan')
            if isinstance(val, str) and val.strip() == "":
                val = None
            if isinstance(val, str) and val.strip().lower() == "nan":
                val = None
            if isinstance(val, float):
                try:
                    if math.isnan(val):
                        val = None
                except Exception:
                    pass

            # Coerce numeric-like strings to numeric types
            val = _coerce_numeric(val)

            item[k] = val

        normalized.append(item)

    return normalized