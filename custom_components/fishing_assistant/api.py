"""Open-Meteo API client and normalizer for Fishing Assistant.

Provides:
- OpenMeteoClient: async fetch of hourly weather + optional marine data
- normalize_hourly_response: converts Open-Meteo `hourly` arrays into a list of
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

The client is defensive: logs raw response keys, checks lengths, and fills missing
values with None. Times are normalized to UTC ISO strings ending with 'Z'.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Iterable

import aiohttp
from homeassistant.util import dt as dt_util

from .const import OPEN_METEO_URL, OPEN_METEO_MARINE_URL

_LOGGER = logging.getLogger(__name__)


class OpenMeteoClient:
    """Client to fetch and normalize Open-Meteo data."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
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

        weather_data = await self._fetch_open_meteo(
            OPEN_METEO_URL,
            latitude,
            longitude,
            list(hourly_vars),
            forecast_days,
        )

        marine_data = None
        if include_marine:
            # Try to fetch marine data from the dedicated marine endpoint. If that fails,
            # attempt to read marine vars from the main endpoint's response (if present).
            try:
                marine_data = await self._fetch_open_meteo(
                    OPEN_METEO_MARINE_URL,
                    latitude,
                    longitude,
                    list(marine_vars),
                    forecast_days,
                    is_marine=True,
                )
            except Exception as exc:
                _LOGGER.debug("Marine endpoint fetch failed: %s; will attempt to merge whatever is available", exc)

            # If marine_data is None but weather_data contains some marine keys, we'll merge those later.

        # Normalize / merge into hourly list
        normalized = normalize_hourly_merged(weather_data, marine_data)

        # Debug logs
        try:
            _LOGGER.debug("Open-Meteo raw keys (weather): %s", list(weather_data.keys()) if isinstance(weather_data, dict) else type(weather_data))
            if marine_data:
                _LOGGER.debug("Open-Meteo raw keys (marine): %s", list(marine_data.keys()) if isinstance(marine_data, dict) else type(marine_data))
            _LOGGER.debug("Normalized forecast count=%d; first_items=%s", len(normalized), normalized[:3])
        except Exception:
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

        try:
            async with session.get(base_url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Open-Meteo returned status {resp.status}: {text}")
                return await resp.json()
        finally:
            if close_session:
                await session.close()


# -----------------------------
# Normalization helpers
# -----------------------------


def _to_utc_iso(dt_val: Any) -> Optional[str]:
    """Parse a datetime-like value and return a UTC ISO string with Z.

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
            # Try convertable types
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


def normalize_hourly_response(raw: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Return the 'hourly' dict from Open-Meteo response or an empty dict.

    The function returns a dict mapping variable->list, with special handling for 'time'.
    """
    if not raw or not isinstance(raw, dict):
        return {}
    hourly = raw.get("hourly") or {}

    # Some responses may place arrays under 'hourly_units' etc. We only return arrays.
    result: Dict[str, List[Any]] = {}
    for k, v in hourly.items():
        if isinstance(v, list):
            result[k] = v
    return result


def normalize_hourly_merged(weather_raw: Optional[Dict[str, Any]], marine_raw: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Merge weather and marine hourly arrays into a normalized list of dicts.

    Uses weather_raw.hourly.time as the canonical timeline when available. If not,
    falls back to marine times or best-effort.
    """
    weather_hourly = normalize_hourly_response(weather_raw)
    marine_hourly = normalize_hourly_response(marine_raw)

    # Determine canonical times
    times = weather_hourly.get("time") or marine_hourly.get("time") or []

    # If times present, normalize them to UTC ISO strings
    times_iso: List[Optional[str]] = [_to_utc_iso(t) for t in times]

    # Collect union of variable keys (exclude 'time')
    keys = set(weather_hourly.keys()) | set(marine_hourly.keys())
    keys.discard("time")

    # Build list of dicts
    normalized: List[Dict[str, Any]] = []

    # If times list is empty but we have some scalar fields, try to form single item
    if not times_iso:
        # Try to build a single snapshot from 'current' like keys
        item: Dict[str, Any] = {"time": None}
        for k in keys:
            vals = weather_hourly.get(k) or marine_hourly.get(k)
            if isinstance(vals, list) and len(vals) > 0:
                item[k] = vals[0]
            else:
                item[k] = vals
        # Convert None time to current UTC
        item["time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [item]

    # Pre-check lengths and log mismatches
    expected_len = len(times_iso)
    for src_name, arr in (("weather", weather_hourly), ("marine", marine_hourly)):
        for k, v in arr.items():
            if k == "time":
                continue
            if isinstance(v, list) and len(v) != expected_len:
                _LOGGER.debug("Variable '%s' from %s has length %d but expected %d", k, src_name, len(v), expected_len)

    # For each index, build item dict
    for idx, t_iso in enumerate(times_iso):
        item: Dict[str, Any] = {}
        item["time"] = t_iso
        for k in keys:
            # Prefer weather arrays, fallback to marine arrays
            val = None
            src_arr = weather_hourly.get(k)
            if isinstance(src_arr, list):
                if idx < len(src_arr):
                    val = src_arr[idx]
                else:
                    val = None
            else:
                # not a list - could be scalar
                val = src_arr

            if val is None:
                src_arr2 = marine_hourly.get(k)
                if isinstance(src_arr2, list):
                    if idx < len(src_arr2):
                        val = src_arr2[idx]
                else:
                    if src_arr2 is not None:
                        val = src_arr2

            # Final coercion: convert empty strings to None
            if val == "" or (isinstance(val, float) and (val != val)):
                val = None

            item[k] = val
        normalized.append(item)

    return normalized