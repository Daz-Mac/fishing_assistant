"""Marine data fetcher for Open-Meteo Marine API with defensive parsing and normalization."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import aiohttp
from homeassistant.util import dt as dt_util

from .const import OPEN_METEO_MARINE_URL
from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)

# Default cache TTL (seconds)
_DEFAULT_CACHE_TTL = 3600  # 1 hour


class MarineDataFetcher:
    """Fetch marine weather data (current + forecast) from Open-Meteo."""

    def __init__(self, hass, latitude: float, longitude: float, cache_ttl: int = _DEFAULT_CACHE_TTL):
        """Initialize the marine data fetcher."""
        self.hass = hass
        self.latitude = float(latitude or 0.0)
        self.longitude = float(longitude or 0.0)
        self._last_fetch: Optional[datetime] = None
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_ttl = int(cache_ttl)

    async def get_marine_data(self) -> Dict[str, Any]:
        """Return normalized marine data, using cache when fresh."""
        now = dt_util.now()
        try:
            if self._last_fetch and self._cache:
                age = (now - self._last_fetch).total_seconds()
                if age < self._cache_ttl:
                    return self._cache
        except Exception:
            # Defensive: if time arithmetic fails, ignore cache
            _LOGGER.debug("Cache check failed; will fetch new marine data", exc_info=True)

        try:
            raw = await self._fetch_from_api()
            # Normalize into canonical shape using DataFormatter
            normalized = DataFormatter.format_marine_data(raw if isinstance(raw, dict) else {})
            # Attach metadata
            normalized.setdefault("source", "open-meteo")
            normalized.setdefault("last_updated", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            # Cache normalized form
            self._cache = normalized
            self._last_fetch = now
            return normalized
        except Exception as exc:
            _LOGGER.exception("Error fetching marine data from API; returning fallback: %s", exc)
            fallback = self._get_fallback_data()
            # Ensure normalized fallback
            normalized = DataFormatter.format_marine_data(fallback)
            normalized.setdefault("source", fallback.get("source", "unavailable"))
            normalized.setdefault("last_updated", fallback.get("last_updated", dt_util.now().strftime("%Y-%m-%dT%H:%M:%SZ")))
            self._cache = normalized
            self._last_fetch = dt_util.now()
            return normalized

    async def _fetch_from_api(self) -> Dict[str, Any]:
        """Fetch raw data from Open-Meteo Marine API and parse hourly arrays into a dict."""
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": ",".join(
                [
                    "wave_height",
                    "wave_direction",
                    "wave_period",
                    "wind_wave_height",
                    "wind_wave_direction",
                    "wind_wave_period",
                    "swell_wave_height",
                    "swell_wave_direction",
                    "swell_wave_period",
                ]
            ),
            "timezone": "UTC",
            "forecast_days": 7,
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(OPEN_METEO_MARINE_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()[:1000]
                    raise RuntimeError(f"Open-Meteo returned status {resp.status}: {text}")
                data = await resp.json()
                return self._parse_marine_data(data)

    def _parse_marine_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the Open-Meteo response into a simple raw dict suitable for normalization.

        The returned dict will contain 'current' and 'forecast' keys. Timestamps in 'current'
        will be provided as ISO Z strings; forecast will be aggregated by date.
        """
        if not raw_data or not isinstance(raw_data, dict):
            raise ValueError("Empty or invalid response from marine API")

        hourly = raw_data.get("hourly") or {}
        times = hourly.get("time") or []

        # Parse times into timezone-aware datetimes in UTC
        parsed_times: List[datetime] = []
        for t in times:
            try:
                dt = dt_util.parse_datetime(str(t))
                if not dt:
                    continue
                if dt.tzinfo is None:
                    # assume UTC if timezone missing
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt_util.as_utc(dt)
                parsed_times.append(dt)
            except Exception:
                # skip unparseable entries
                continue

        if not parsed_times:
            raise ValueError("No parseable hourly times in marine API response")

        now = dt_util.now()
        # Find index of the latest time <= now (or 0 if all in future)
        current_index = 0
        try:
            for i, t in enumerate(parsed_times):
                if t <= now:
                    current_index = i
                else:
                    break
        except Exception:
            current_index = 0

        def _safe_get_list_value(lst: Any, idx: int):
            if not isinstance(lst, list):
                return None
            if idx < 0 or idx >= len(lst):
                return None
            return lst[idx]

        # Build current snapshot from hourly arrays (defensive)
        current = {
            "wave_height": _safe_get_list_value(hourly.get("wave_height"), current_index),
            "wave_period": _safe_get_list_value(hourly.get("wave_period"), current_index),
            "wave_direction": _safe_get_list_value(hourly.get("wave_direction"), current_index),
            "wind_wave_height": _safe_get_list_value(hourly.get("wind_wave_height"), current_index),
            "wind_wave_period": _safe_get_list_value(hourly.get("wind_wave_period"), current_index),
            "swell_wave_height": _safe_get_list_value(hourly.get("swell_wave_height"), current_index),
            "swell_wave_period": _safe_get_list_value(hourly.get("swell_wave_period"), current_index),
            "timestamp": parsed_times[current_index].strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Aggregate daily forecast from hourly arrays
        daily_buckets: Dict[str, Dict[str, List[float]]] = {}
        # Keys we care about and their hourly names
        mapping = {
            "wave_height": "wave_height",
            "wave_period": "wave_period",
            "wind_wave_height": "wind_wave_height",
            "swell_wave_height": "swell_wave_height",
        }

        # Iterate positions defensively
        for idx, dt in enumerate(parsed_times):
            date_key = dt.date().isoformat()
            bucket = daily_buckets.setdefault(
                date_key,
                {"wave_height": [], "wave_period": [], "wind_wave_height": [], "swell_wave_height": []},
            )

            for out_key, hourly_key in mapping.items():
                try:
                    val = _safe_get_list_value(hourly.get(hourly_key), idx)
                    if val is None:
                        continue
                    # Attempt numeric coercion
                    try:
                        fval = float(val)
                    except Exception:
                        continue
                    bucket[out_key].append(fval)
                except Exception:
                    # Keep robust: ignore any errors parsing values
                    continue

        # Compute daily statistics
        forecast: Dict[str, Any] = {}
        for date_key, values in daily_buckets.items():
            def _safe_agg(lst: List[float]) -> Dict[str, Optional[float]]:
                if not lst:
                    return {"min": None, "avg": None, "max": None}
                try:
                    mn = min(lst)
                    mx = max(lst)
                    avg = sum(lst) / len(lst)
                    return {"min": mn, "avg": avg, "max": mx}
                except Exception:
                    return {"min": None, "avg": None, "max": None}

            wave_h = _safe_agg(values.get("wave_height", []))
            wave_p = _safe_agg(values.get("wave_period", []))
            wind_wave_h = _safe_agg(values.get("wind_wave_height", []))
            swell_h = _safe_agg(values.get("swell_wave_height", []))

            forecast[date_key] = {
                "wave_height_max": wave_h["max"],
                "wave_height_avg": wave_h["avg"],
                "wave_height_min": wave_h["min"],
                "wave_period_avg": wave_p["avg"],
                "wind_wave_height_max": wind_wave_h["max"],
                "swell_wave_height_max": swell_h["max"],
            }

        return {
            "current": current,
            "forecast": forecast,
            "source": "open-meteo",
            "last_updated": dt_util.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _get_fallback_data(self) -> Dict[str, Any]:
        """Return fallback data when API fails (timestamps as ISO Z strings)."""
        now_iso = dt_util.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "current": {
                "wave_height": None,
                "wave_period": None,
                "wave_direction": None,
                "wind_wave_height": None,
                "wind_wave_period": None,
                "swell_wave_height": None,
                "swell_wave_period": None,
                "timestamp": now_iso,
            },
            "forecast": {},
            "source": "unavailable",
            "last_updated": now_iso,
        }

    def get_current_wave_height(self) -> Optional[float]:
        """Return current wave height (meters) from cached normalized data, or None."""
        try:
            if not self._cache:
                return None
            cur = (self._cache.get("current") or {})
            val = cur.get("wave_height")
            if val is None:
                return None
            return float(val)
        except Exception:
            _LOGGER.debug("Failed to read current wave height from cache", exc_info=True)
            return None

    def get_current_wave_period(self) -> Optional[float]:
        """Return current wave period (seconds) from cached normalized data, or None."""
        try:
            if not self._cache:
                return None
            cur = (self._cache.get("current") or {})
            val = cur.get("wave_period")
            if val is None:
                return None
            return float(val)
        except Exception:
            _LOGGER.debug("Failed to read current wave period from cache", exc_info=True)
            return None

    def get_wave_condition_score(self, max_wave_height: float = 2.0) -> int:
        """
        Score wave conditions (0-100).
        Higher scores for moderate waves, lower for calm or rough.
        """
        try:
            wave_height = self.get_current_wave_height()
            if wave_height is None:
                return 50  # neutral

            max_h = float(max_wave_height or 2.0)
            if wave_height > max_h:
                return 0
            if wave_height < 0.3:
                return 60
            if 0.5 <= wave_height <= 1.5:
                return 100
            if 0.3 <= wave_height < 0.5:
                return 80
            # linear penalty between 1.5 and max_h
            if wave_height > 1.5:
                denom = (max_h - 1.5) if (max_h > 1.5) else 0.01
                penalty = (wave_height - 1.5) / denom
                score = max(0, int(100 - (penalty * 100)))
                return score
            return 50
        except Exception:
            _LOGGER.exception("Failed to compute wave condition score; returning neutral")
            return 50

    def is_safe_conditions(self, max_wave_height: float = 2.0) -> bool:
        """Return True if current wave height is below or equal to max_wave_height, or if unknown."""
        try:
            wave_height = self.get_current_wave_height()
            if wave_height is None:
                return True
            return float(wave_height) <= float(max_wave_height or 2.0)
        except Exception:
            _LOGGER.debug("Failed to evaluate safety; assuming safe", exc_info=True)
            return True