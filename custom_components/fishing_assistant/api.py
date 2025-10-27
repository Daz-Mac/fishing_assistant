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

This version hardens parsing, improves logging, is defensive about missing keys,
coerces numeric-like strings to numbers, normalizes times to UTC ISO strings
(with 'Z' suffix), and ensures predictable list shapes for downstream consumers.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import aiohttp
from homeassistant.util import dt as dt_util

from .const import OPEN_METEO_MARINE_URL, OPEN_METEO_URL

_LOGGER = logging.getLogger(__name__)


class OpenMeteoClient:
    """Async client to fetch and normalize Open-Meteo data."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None) -> None:
        # If a session is supplied, we won't close it.
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
        if hourly_vars is None:
            hourly_vars = (
                "temperature_2m",
                "cloudcover",
                "precipitation",
                "wind_speed_10m",
                "pressure_msl",
            )
        if marine_vars is None:
            marine_vars = (
                "wave_height",
                "wave_period",
                "sea_surface_temperature",
            )

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
            except Exception as exc:  # defensive: don't break flow due to marine failure
                _LOGGER.debug(
                    "Open-Meteo marine fetch failed; proceeding without marine. Error: %s", exc
                )

        normalized = normalize_hourly_merged(weather_data, marine_data)

        # Additional debug (non-fatal)
        try:
            _LOGGER.debug(
                "Open-Meteo normalized forecast count=%d; first_items=%s",
                len(normalized),
                normalized[:3],
            )
        except Exception:
            # Never raise from logging
            _LOGGER.debug("Failed to log normalized forecast preview")

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
        """Perform HTTP GET to Open-Meteo and return parsed JSON.

        Raises RuntimeError for non-200 responses or JSON parse failure.
        This method is defensive and logs request/response metadata for debugging.
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
            timeout = aiohttp.ClientTimeout(total=15)  # seconds; adjust if you want shorter/longer
            async with session.get(base_url, params=params, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.debug(
                        "Open-Meteo non-200 response: status=%s body=%s", resp.status, text
                    )
                    raise RuntimeError(f"Open-Meteo returned status {resp.status}")

                try:
                    # tolerate content-types that may not be exact JSON MIME
                    json_data = await resp.json(content_type=None)
                    if not isinstance(json_data, dict):
                        _LOGGER.debug("Open-Meteo returned non-dict JSON: %s", type(json_data))
                    return json_data
                except Exception as exc:
                    _LOGGER.debug(
                        "Failed to parse Open-Meteo JSON: %s; raw body (truncated)=%s",
                        exc,
                        (text or "")[:1000],
                    )
                    raise
        finally:
            if close_session:
                try:
                    await session.close()
                except Exception:
                    _LOGGER.debug("Error closing temporary aiohttp session", exc_info=True)


# -----------------------------
# Normalization helpers
# -----------------------------


def _to_utc_iso(dt_val: Any) -> Optional[str]:
    """Parse a datetime-like value and return UTC ISO string with 'Z' suffix.

    Accepts datetime objects or strings parsable by Home Assistant's dt_util.parse_datetime.
    If parsing fails returns None.

    Note: dt_util.parse_datetime may return a naive datetime interpreted as local time.
    Here we treat naive datetimes as UTC to avoid accidental timezone shifts in normalized
    output (this matches upstream use in this integration). If you prefer local->UTC
    conversion, adjust accordingly.
    """
    if dt_val is None:
        return None
    try:
        if isinstance(dt_val, datetime):
            parsed = dt_val
        else:
            parsed = dt_util.parse_datetime(str(dt_val))
        if parsed is None:
            return None

        # Treat naive datetimes as UTC (explicit) to keep normalization deterministic
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed_utc = parsed.astimezone(timezone.utc)
        return parsed_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        _LOGGER.debug("Failed to parse/normalize time value: %s", dt_val, exc_info=True)
        return None


def _coerce_numeric(value: Any) -> Any:
    """Coerce numeric-like values to int/float; leave booleans alone.

    Returns:
    - int for integer-like values
    - float for decimal-like values
    - None for empty/"nan"/NaN
    - original value for non-numeric strings
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            if isinstance(value, float) and math.isnan(value):
                return None
        except Exception:
            pass
        return value
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        if s.lower() == "nan":
            return None
        # Try integer-ish
        try:
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                return int(s)
            f = float(s)
            if math.isnan(f):
                return None
            # Prefer int when the float is integral
            if abs(f - int(f)) < 1e-9:
                return int(f)
            return f
        except Exception:
            return value
    # Fallback: return original
    return value


def normalize_hourly_response(raw: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Return the 'hourly' dict from an Open-Meteo response in a safe form.

    Ensures every returned key maps to a list. Scalars are converted to single-element lists.
    Returns an empty dict for invalid input.
    """
    if not raw or not isinstance(raw, dict):
        return {}
    hourly = raw.get("hourly") or {}
    out: Dict[str, List[Any]] = {}
    for k, v in hourly.items():
        if isinstance(v, list):
            out[k] = v
        else:
            # Convert scalars -> single-element list for consistent indexing
            out[k] = [v]
    return out


def normalize_hourly_merged(
    weather_raw: Optional[Dict[str, Any]], marine_raw: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Merge weather and marine hourly arrays from Open-Meteo into a normalized list.

    - Uses weather_raw.hourly.time as canonical timeline where available, otherwise marine time.
    - Coerces numeric-like strings to numbers.
    - Converts empty/"nan"/NaN to None.
    - Normalizes times to UTC ISO strings with 'Z' suffix.
    """
    weather_hourly = normalize_hourly_response(weather_raw)
    marine_hourly = normalize_hourly_response(marine_raw)

    # Determine canonical times
    times = weather_hourly.get("time") or marine_hourly.get("time") or []

    # Normalize times to UTC ISO strings preserving index alignment
    times_iso: List[Optional[str]] = [_to_utc_iso(t) for t in times]
    expected_len = len(times_iso)

    # Collect keys excluding 'time'
    keys = set(weather_hourly.keys()) | set(marine_hourly.keys())
    keys.discard("time")

    # If we have no timeline but some scalar fields, return a single snapshot with first values
    if expected_len == 0:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        item: Dict[str, Any] = {"time": now_iso}
        for k in keys:
            vals = weather_hourly.get(k) or marine_hourly.get(k)
            chosen = None
            if isinstance(vals, list) and len(vals) > 0:
                chosen = vals[0]
            else:
                chosen = None
            chosen = _coerce_numeric(chosen)
            item[k] = chosen
        return [item]

    # Log any length mismatches (non-fatal): helpful for debugging inconsistent upstream data
    for src_name, arr in (("weather", weather_hourly), ("marine", marine_hourly)):
        for k, v in arr.items():
            if k == "time":
                continue
            if isinstance(v, list) and len(v) != expected_len:
                _LOGGER.debug(
                    "Open-Meteo variable '%s' from %s has length %d but expected %d (times length).",
                    k,
                    src_name,
                    len(v),
                    expected_len,
                )

    # Build normalized list
    normalized: List[Dict[str, Any]] = []
    for idx in range(expected_len):
        t_iso = times_iso[idx] if idx < len(times_iso) else None
        if t_iso is None:
            _LOGGER.debug(
                "Unparseable/missing time at index %d raw=%s",
                idx,
                times[idx] if idx < len(times) else None,
            )

        item: Dict[str, Any] = {"time": t_iso}
        for k in keys:
            val: Any = None

            # Prefer weather source
            src_arr = weather_hourly.get(k)
            if isinstance(src_arr, list):
                val = src_arr[idx] if idx < len(src_arr) else None
            else:
                # Scalar -> broadcast
                if src_arr is not None:
                    val = src_arr

            # Fallback to marine source
            if val is None:
                src_arr2 = marine_hourly.get(k)
                if isinstance(src_arr2, list):
                    val = src_arr2[idx] if idx < len(src_arr2) else None
                else:
                    if src_arr2 is not None:
                        val = src_arr2

            # Normalize sentinel values
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

            # Coerce numeric-like strings -> numbers
            val = _coerce_numeric(val)

            item[k] = val

        normalized.append(item)

    return normalized