"""Astronomical tide proxy calculator (defensive, normalized output)."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from homeassistant.util import dt as dt_util

from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)

# Cache TTL for proxy calculations (seconds)
_DEFAULT_TTL = 15 * 60  # 15 minutes


class TideProxy:
    """Calculate tide state using simplified astronomical proxies (sun/moon)."""

    def __init__(self, hass, latitude: float, longitude: float, ttl: int = _DEFAULT_TTL):
        """Initialize the tide proxy."""
        self.hass = hass
        self.latitude = float(latitude or 0.0)
        self.longitude = float(longitude or 0.0)
        self._last_calculation: Optional[datetime] = None
        self._cache: Optional[Dict[str, Any]] = None
        self._ttl = int(ttl)

    async def get_tide_data(self) -> Dict[str, Any]:
        """Get current tide state and predictions (normalized)."""
        now = dt_util.now()

        # Return cached result if fresh
        try:
            if self._last_calculation and self._cache:
                age = (now - self._last_calculation).total_seconds()
                if age < self._ttl:
                    return self._cache
        except Exception:
            _LOGGER.debug("Cache age check failed; recalculating tide proxy", exc_info=True)

        try:
            moon_data = await self._get_moon_data()
            sun_data = await self._get_sun_data()
        except Exception as exc:
            _LOGGER.exception("Failed to retrieve sun/moon data for tide proxy: %s", exc)
            moon_data = {"phase": None, "altitude": None}
            sun_data = {"elevation": None}

        try:
            state = self._calculate_tide_state(moon_data, sun_data, dt_util.now())
            strength = self._calculate_tide_strength(moon_data)
            next_high_dt, next_low_dt = self._predict_tide_changes(moon_data, dt_util.now())

            raw_tide = {
                "state": state,
                "strength": int(strength),
                # Use ISO Z strings for next_* fields for portability
                "next_high": next_high_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if next_high_dt else "",
                "next_low": next_low_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if next_low_dt else "",
                "confidence": "proxy",
                "source": "astronomical_calculation",
            }

            # Build a simple per-day tide forecast using astronomical sampling (moon phase at local solar noon)
            try:
                # Import here to avoid skyfield dependency at module import time
                from .helpers.astro import calculate_astronomy_forecast  # local import for optional feature

                try:
                    astro_forecast = await calculate_astronomy_forecast(self.hass, self.latitude, self.longitude, days=7)
                except Exception:
                    astro_forecast = {}

                forecast: Dict[str, Any] = {}
                for date_str, a in (astro_forecast or {}).items():
                    try:
                        moon_phase = a.get("moon_phase") if isinstance(a, dict) else None

                        # Prefer the moon_transit sampling time if present; otherwise use local noon UTC
                        dt_sample = None
                        mt = a.get("moon_transit") if isinstance(a, dict) else None
                        if mt:
                            try:
                                dt_sample = dt_util.parse_datetime(mt)
                            except Exception:
                                dt_sample = None

                        if dt_sample is None:
                            try:
                                d = datetime.fromisoformat(date_str)
                                dt_sample = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                            except Exception:
                                dt_sample = datetime.now(timezone.utc)

                        moon_data_day = {"phase": moon_phase, "altitude": None}
                        state_day = self._calculate_tide_state(moon_data_day, {"elevation": None}, dt_sample)
                        strength_day = self._calculate_tide_strength(moon_data_day)

                        forecast[date_str] = {
                            "state": state_day,
                            "strength": int(strength_day),
                            "datetime": dt_sample.strftime("%Y-%m-%dT%H:%M:%SZ") if dt_sample else "",
                            "source": "astronomical_calculation",
                        }
                    except Exception:
                        # Skip problematic days but continue building forecast
                        _LOGGER.debug("Failed to build tide forecast entry for %s", date_str, exc_info=True)

                if forecast:
                    raw_tide["forecast"] = forecast
            except Exception:
                _LOGGER.debug("Astronomical tide forecast generation unavailable; continuing with snapshot only", exc_info=True)

            # Normalize via DataFormatter to ensure callers receive expected shape
            normalized = DataFormatter.format_tide_data(raw_tide)

            # Cache a copy
            self._cache = normalized
            self._last_calculation = now

            return normalized

        except Exception as exc:
            _LOGGER.exception("Failed to compute tide proxy: %s", exc)
            fallback = DataFormatter.format_tide_data(None)
            # add meta for source
            fallback["source"] = "astronomical_calculation"
            fallback["confidence"] = "proxy"
            self._cache = fallback
            self._last_calculation = now
            return fallback

    async def _get_moon_data(self) -> Dict[str, Optional[float]]:
        """Compute moon phase (0..1) and current altitude using internal astronomy code.

        This method no longer reads any Home Assistant moon sensor entities. It uses
        the integration's own astronomy calculator (helpers.astro) to compute a
        per-day moon phase and attempts to compute the instantaneous moon altitude
        via Skyfield (ephemeris). If altitude calculation fails, altitude is left
        as None (no heuristic fallbacks).
        Returns: {"phase": float|None, "altitude": float|None}
        """
        try:
            # Prefer the integration's own astronomy forecast to obtain a per-day moon_phase
            from .helpers.astro import calculate_astronomy_forecast

            try:
                astro_forecast = await calculate_astronomy_forecast(self.hass, self.latitude, self.longitude, days=2)
            except Exception:
                astro_forecast = {}

            today_iso = dt_util.as_local(dt_util.now()).date().isoformat()
            phase_val: Optional[float] = None
            altitude: Optional[float] = None

            if isinstance(astro_forecast, dict):
                today_entry = astro_forecast.get(today_iso) or {}
                if isinstance(today_entry, dict):
                    phase_val = today_entry.get("moon_phase")

            # Attempt to compute instantaneous moon altitude using Skyfield (optional import)
            try:
                # Local import to avoid hard dependency at module import time
                from skyfield.api import load, wgs84  # type: ignore
                import os

                # Reuse same ephemeris path as helpers.astro (data/de421.bsp)
                data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
                eph_path = os.path.join(data_dir, "de421.bsp")

                def _load_eph():
                    return load(eph_path)

                # Load ephemeris in executor to avoid blocking the event loop
                eph = await self.hass.async_add_executor_job(_load_eph)
                ts = load.timescale()

                now = dt_util.now()
                # Build skyfield Time from current UTC components
                t = ts.utc(now.year, now.month, now.day, now.hour, now.minute, now.second)

                earth = eph["earth"]
                moon = eph["moon"]
                location = wgs84.latlon(self.latitude, self.longitude)

                # Compute apparent position of the moon from the location (altitude)
                astrom = earth.at(t).observe(moon).apparent()
                alt, az, distance = astrom.altaz()
                try:
                    altitude = float(getattr(alt, "degrees", None))
                except Exception:
                    # alt may be an Angle-like object; attempt conversion
                    try:
                        altitude = float(alt.degrees)
                    except Exception:
                        altitude = None
            except Exception:
                # Do not fallback to heuristics 	6 keep altitude None if calculation fails
                altitude = None

            return {"phase": phase_val, "altitude": altitude}
        except Exception:
            _LOGGER.exception("Error computing moon data internally")
            return {"phase": None, "altitude": None}

    async def _get_sun_data(self) -> Dict[str, Optional[float]]:
        """Compute sun elevation (degrees) using Skyfield ephemeris when available.

        This method intentionally does NOT read the Home Assistant `sun.sun` entity.
        If Skyfield or the ephemeris is unavailable, return {"elevation": None}.
        """
        try:
            # Local import to avoid hard dependency at module import time
            from skyfield.api import load, wgs84  # type: ignore
            import os

            # Reuse same ephemeris path as used elsewhere (data/de421.bsp)
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
            eph_path = os.path.join(data_dir, "de421.bsp")

            def _load_eph():
                return load(eph_path)

            # Load ephemeris in executor to avoid blocking the event loop
            eph = await self.hass.async_add_executor_job(_load_eph)
            ts = load.timescale()

            now = dt_util.now()
            # Build skyfield Time from current UTC components
            t = ts.utc(now.year, now.month, now.day, now.hour, now.minute, now.second)

            earth = eph["earth"]
            sun_obj = eph["sun"]
            location = wgs84.latlon(self.latitude, self.longitude)

            # Compute apparent position of the sun from the location (altitude)
            astrom = earth.at(t).observe(sun_obj).apparent()
            alt, az, distance = astrom.altaz()
            try:
                elevation = float(getattr(alt, "degrees", None))
            except Exception:
                # alt may be Angle-like; attempt to access .degrees
                try:
                    elevation = float(alt.degrees)
                except Exception:
                    elevation = None

            return {"elevation": elevation}
        except Exception:
            # Do not fallback to reading HA entity; if Skyfield fails, return None
            _LOGGER.debug("Skyfield sun elevation calculation failed; returning None", exc_info=True)
            return {"elevation": None}

    def _calculate_tide_state(self, moon_data: Dict[str, Optional[float]], sun_data: Dict[str, Optional[float]], now: datetime) -> str:
        """Determine tide state (rising/falling/slack_high/slack_low) using a simple heuristic."""
        try:
            moon_alt = moon_data.get("altitude")
            if moon_alt is None:
                # Fallback to simple rise/fall guess
                rising = self._is_moon_rising(now)
                return "rising" if rising else "falling"

            # Use thresholds for 'slack' indicators
            if abs(moon_alt) > 70:
                return "slack_high"
            if abs(moon_alt) < 10:
                return "slack_low"

            # Determine rising/falling from lunar cycle position
            if self._is_moon_rising(now):
                return "rising"
            return "falling"
        except Exception:
            _LOGGER.exception("Error calculating tide state; defaulting to 'unknown'")
            return "unknown"

    def _is_moon_rising(self, now: datetime) -> bool:
        """Heuristic: determine if moon is in rising half of its local cycle.

        Uses lunar-day (~24.84h) modulo of epoch hours to reduce sensitivity to local hour edge cases.
        """
        try:
            lunar_day_hours = 24.84
            hours_since_epoch = now.timestamp() / 3600.0
            frac = (hours_since_epoch % lunar_day_hours) / lunar_day_hours
            # rising if in first half of cycle
            return frac < 0.5
        except Exception:
            # fallback to local hour heuristic
            hour = now.hour + now.minute / 60.0
            return 0 < hour < 12.42

    def _calculate_tide_strength(self, moon_data: Dict[str, Optional[float]]) -> int:
        """Estimate tide strength 0..100 based on lunar phase (spring/neap)."""
        try:
            phase = moon_data.get("phase")
            if phase is None:
                return 50
            # Ensure numeric in 0..1
            try:
                p = float(phase)
            except Exception:
                return 50
            p = max(0.0, min(1.0, p))

            # Strong (spring) near new (0) and full (~0.5). We compute distance to nearest spring phase.
            # Dist to new:
            dist_new = abs(p - 0.0)
            dist_full = abs(p - 0.5)
            # distance to nearest spring (min of the two)
            dist = min(dist_new, dist_full)
            # map [0..0.25] -> strength [100..0] (quarters are neap)
            strength = max(0.0, 1.0 - (dist / 0.25)) * 100.0
            return int(round(max(0.0, min(100.0, strength))))
        except Exception:
            _LOGGER.exception("Error computing tide strength; returning neutral")
            return 50

    def _predict_tide_changes(self, moon_data: Dict[str, Optional[float]], now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Predict next high and low tide datetimes (UTC) using a simple semi-diurnal model.

        Returns (next_high_dt, next_low_dt) as timezone-aware datetimes in UTC or (None, None) on failure.
        """
        try:
            # Semi-diurnal half-cycle ~12.42 hours
            half_cycle = 12.42
            # Use fractional position in a cycle (based on epoch hours) to estimate time until next high tide
            hours_since_epoch = now.timestamp() / 3600.0
            frac = (hours_since_epoch % half_cycle) / half_cycle
            # If frac == 0 -> it's a high; next high is half_cycle hours away, else next high is remainder to complete cycle
            remainder = (1.0 - frac) * half_cycle
            next_high_hours = remainder if remainder > 0.01 else half_cycle
            next_low_hours = next_high_hours + (half_cycle / 2.0)

            next_high = (now + timedelta(hours=next_high_hours)).astimezone(timezone.utc)
            next_low = (now + timedelta(hours=next_low_hours)).astimezone(timezone.utc)

            return next_high, next_low
        except Exception:
            _LOGGER.exception("Error predicting tide change times")
            return None, None

    @staticmethod
    def _coerce_phase(val: Any) -> Optional[float]:
        """Try to convert a value (string/name/number) to a float in [0,1] representing moon phase.

        Accepts numeric strings, floats, or common named states.
        """
        try:
            if val is None:
                return None
            if isinstance(val, (int, float)):
                v = float(val)
                # If value looks like 0-100 scale, normalize
                if v > 1.0:
                    v = max(0.0, min(100.0, v)) / 100.0
                return max(0.0, min(1.0, v))
            s = str(val).strip().lower()
            # common names map
            names = {
                "new_moon": 0.0,
                "new": 0.0,
                "waxing_crescent": 0.125,
                "first_quarter": 0.25,
                "waxing_gibbous": 0.375,
                "full_moon": 0.5,
                "full": 0.5,
                "waning_gibbous": 0.625,
                "last_quarter": 0.75,
                "waning_crescent": 0.875,
                "0": 0.0,
                "0.0": 0.0,
            }
            if s in names:
                return float(names[s])
            # try numeric parse
            try:
                f = float(s)
                if f > 1.0:
                    f = max(0.0, min(100.0, f)) / 100.0
                return max(0.0, min(1.0, f))
            except Exception:
                return None
        except Exception:
            return None