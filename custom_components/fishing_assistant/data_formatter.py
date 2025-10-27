"""Data formatting and integration layer for Fishing Assistant.

Provides DataFormatter which converts raw API/provider data into the integration's
canonical shapes (as plain dicts that conform to the TypedDicts in data_schema.py).

This hardened version:
- Is defensive about missing/variant keys (camelCase vs snake_case).
- Ensures stable return types (never returns None where a dict is expected).
- Normalizes numeric types and datetime strings.
- Handles common alternate shapes for marine/tide/forecast payloads.
- Adds small guards so sensors can safely call these helpers.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .data_schema import (
    AstroData,
    ComponentScores,
    DailyForecast,
    PeriodForecast,
    ScoringResult,
    SensorAttributes,
    TideData,
    WeatherData,
)

_LOGGER = logging.getLogger(__name__)


def _safe_float(val: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """Safely convert val to float; return default (or None) on failure.

    If default is None, function returns None on failure; otherwise returns default.
    """
    if val is None:
        return None if default is None else default
    # Preserve booleans as numeric (True -> 1.0) only if caller passed numeric defaults
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        try:
            if isinstance(val, float) and math.isnan(val):
                return None if default is None else default
        except Exception:
            pass
        return float(val)
    # Strings and other types
    try:
        s = str(val).strip()
        if s == "":
            return None if default is None else default
        if s.lower() == "nan":
            return None if default is None else default
        f = float(s)
        if math.isnan(f):
            return None if default is None else default
        # Prefer integer when it is integral
        if abs(f - int(f)) < 1e-9:
            return float(int(f))
        return f
    except Exception:
        return None if default is None else default


def _iso_now_z() -> str:
    """Return current UTC time as ISO string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DataFormatter:
    """Formatter utilities to produce canonical dict shapes used across the integration."""

    # -----------------
    # Weather
    # -----------------
    @staticmethod
    def format_weather_data(raw_weather: Optional[Dict[str, Any]]) -> WeatherData:
        """Convert raw weather dict into canonical WeatherData shape.

        Tolerant to keys:
          temperature, temperature_2m, temp
          wind_speed, wind_speed_10m, wind
          wind_gust, gust
          cloud_cover, cloudcover, clouds
          precipitation, precipitation_probability, precip
          pressure, pressure_msl
          time, datetime
        """
        if not raw_weather or not isinstance(raw_weather, dict):
            return {
                "temperature": None,
                "wind_speed": None,
                "wind_gust": None,
                "cloud_cover": None,
                "precipitation_probability": None,
                "pressure": None,
                "datetime": _iso_now_z(),
            }

        # Helper to pick first non-None from candidates
        def pick(*keys: Sequence[str]) -> Any:
            for k in keys:
                if k in raw_weather and raw_weather.get(k) is not None:
                    return raw_weather.get(k)
            return None

        temp = pick("temperature", "temperature_2m", "temp", "air_temperature")
        wind = pick("wind_speed", "wind_speed_10m", "wind", "windspeed")
        wind_gust = pick("wind_gust", "gust", "wind_gust_10m", "wind_speed") or wind
        cloud = pick("cloud_cover", "cloudcover", "clouds")
        precip = pick("precipitation_probability", "precipitation", "precip", "rain", "rain_probability")
        pressure = pick("pressure", "pressure_msl", "msl_pressure") or 1013.25
        dt_raw = pick("datetime", "time", "time_utc", "timestamp")

        # Normalize datetime to ISO Z string
        dt_str = None
        try:
            if isinstance(dt_raw, datetime):
                dt_val = dt_raw
                if dt_val.tzinfo is None:
                    dt_val = dt_val.replace(tzinfo=timezone.utc)
                dt_str = dt_val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif isinstance(dt_raw, str) and dt_raw.strip():
                # If it's an ISO-like string, try to normalize; fall back to raw string if parse fails
                try:
                    # Accept date with or without T; fromisoformat accepts 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
                    parsed = datetime.fromisoformat(dt_raw.replace("Z", "+00:00")) if "Z" in dt_raw or "+" in dt_raw or "-" in dt_raw else datetime.fromisoformat(dt_raw)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    dt_str = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    # Last-resort: keep original trimmed string
                    dt_str = dt_raw.strip()
            else:
                dt_str = _iso_now_z()
        except Exception:
            dt_str = _iso_now_z()

        # Coerce numerics
        temp_f = _safe_float(temp, None)
        wind_f = _safe_float(wind, None)
        wind_gust_f = _safe_float(wind_gust, wind_f if wind_f is not None else 0.0)
        cloud_f = _safe_float(cloud, None)
        precip_f = _safe_float(precip, None)
        pressure_f = _safe_float(pressure, None)

        return {
            "temperature": temp_f,
            "wind_speed": wind_f,
            "wind_gust": wind_gust_f,
            "cloud_cover": cloud_f,
            "precipitation_probability": precip_f,
            "pressure": pressure_f,
            "datetime": dt_str or _iso_now_z(),
        }

    # -----------------
    # Marine
    # -----------------
    @staticmethod
    def _extract_marine_value(container: Dict[str, Any], *candidates: Sequence[str]) -> Any:
        for k in candidates:
            if k in container and container.get(k) is not None:
                return container.get(k)
        return None

    @staticmethod
    def format_marine_data(raw_marine: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert raw marine data into consistent nested shape.

        Accepts:
          - provider current snapshot (top-level metrics)
          - {"current": {...}, "forecast": {...}}
          - hourly arrays under raw_marine["hourly"] (picks first index)
        Always returns {"current": {...}, "forecast": {...}} where "current" fields are normalized.
        """
        default_current = {
            "wave_height": None,
            "wave_period": None,
            "wave_direction": None,
            "wind_wave_height": None,
            "wind_wave_period": None,
            "swell_wave_height": None,
            "swell_wave_period": None,
            "timestamp": _iso_now_z(),
        }

        if not raw_marine or not isinstance(raw_marine, dict):
            return {"current": default_current, "forecast": {}}

        try:
            # If nested "current" provided, prefer it
            if isinstance(raw_marine.get("current"), dict):
                cur_src = raw_marine.get("current", {})
            # Some providers include "now" or "latest"
            elif isinstance(raw_marine.get("now"), dict):
                cur_src = raw_marine.get("now", {})
            else:
                # Fall back to top-level keys or first entries from hourly arrays
                cur_src: Dict[str, Any] = {}
                # Try to map common top-level keys
                for key in (
                    "wave_height",
                    "waveHeight",
                    "mean_wave_height",
                    "swell_wave_height",
                    "swellHeight",
                    "peak_period",
                    "wave_period",
                    "wavePeriod",
                    "wave_direction",
                    "timestamp",
                    "time",
                ):
                    if key in raw_marine:
                        cur_src[key] = raw_marine.get(key)

                # If hourly arrays exist (Open-Meteo style), try to pick first index
                hourly = raw_marine.get("hourly") or {}
                if isinstance(hourly, dict):
                    # common names mapping: wave_height, wave_period, time
                    if isinstance(hourly.get("wave_height"), list) and len(hourly["wave_height"]) > 0:
                        cur_src["wave_height"] = hourly["wave_height"][0]
                    if isinstance(hourly.get("wave_period"), list) and len(hourly["wave_period"]) > 0:
                        cur_src["wave_period"] = hourly["wave_period"][0]
                    if isinstance(hourly.get("time"), list) and len(hourly["time"]) > 0:
                        cur_src["time"] = hourly["time"][0]

            # Normalize fields from cur_src (handle camelCase and snake_case)
            wave_h = DataFormatter._extract_marine_value(
                cur_src, "wave_height", "waveHeight", "mean_wave_height", "swell_wave_height"
            )
            wave_p = DataFormatter._extract_marine_value(cur_src, "wave_period", "wavePeriod", "peak_period")
            wave_dir = DataFormatter._extract_marine_value(cur_src, "wave_direction", "waveDirection")
            wind_wave_h = DataFormatter._extract_marine_value(cur_src, "wind_wave_height", "windWaveHeight")
            wind_wave_p = DataFormatter._extract_marine_value(cur_src, "wind_wave_period", "windWavePeriod")
            swell_h = DataFormatter._extract_marine_value(cur_src, "swell_wave_height", "swellHeight")
            swell_p = DataFormatter._extract_marine_value(cur_src, "swell_wave_period", "swellWavePeriod")
            ts = DataFormatter._extract_marine_value(cur_src, "timestamp", "time")

            current = {
                "wave_height": _safe_float(wave_h, None),
                "wave_period": _safe_float(wave_p, None),
                "wave_direction": _safe_float(wave_dir, None),
                "wind_wave_height": _safe_float(wind_wave_h, None),
                "wind_wave_period": _safe_float(wind_wave_p, None),
                "swell_wave_height": _safe_float(swell_h, None),
                "swell_wave_period": _safe_float(swell_p, None),
                "timestamp": str(ts) if ts is not None else _iso_now_z(),
            }

            # Forecast: accept dict or list; convert list->{"items": list} to avoid callers breaking
            forecast_raw = raw_marine.get("forecast") or raw_marine.get("forecasts") or {}
            forecast: Any = {}
            if isinstance(forecast_raw, dict):
                forecast = forecast_raw
            elif isinstance(forecast_raw, list):
                forecast = {"items": forecast_raw}
            else:
                # If hourly arrays exist and appear complete, expose them as forecast.hourly
                hourly_raw = raw_marine.get("hourly")
                if isinstance(hourly_raw, dict):
                    forecast = {"hourly": hourly_raw}

            return {"current": current, "forecast": forecast}
        except Exception as exc:
            _LOGGER.error("Error formatting marine data: %s", exc, exc_info=True)
            return {"current": default_current, "forecast": {}}

    # -----------------
    # Tide
    # -----------------
    @staticmethod
    def format_tide_data(raw_tide: Optional[Dict[str, Any]]) -> TideData:
        """Normalize tide data into a safe TideData dict.

        Accepts vendor variations (state/phase, strength/intensity, next_high/low as strings or datetimes),
        and normalizes forecast lists into {'items': [...] } when needed.
        """
        default: TideData = {
            "state": "unknown",
            "strength": 0,
            "next_high": "",
            "next_low": "",
            "confidence": "unknown",
            "source": "unknown",
            "forecast": {},
        }

        if not raw_tide or not isinstance(raw_tide, dict):
            return default

        try:
            # Accept synonyms
            state = raw_tide.get("state") or raw_tide.get("phase") or raw_tide.get("tide_state") or "unknown"
            strength_raw = raw_tide.get("strength") or raw_tide.get("intensity") or raw_tide.get("strength_percent") or 0
            try:
                strength = int(float(str(strength_raw)))
            except Exception:
                strength = 0

            next_high = raw_tide.get("next_high") or raw_tide.get("nextHigh") or raw_tide.get("high_next") or ""
            next_low = raw_tide.get("next_low") or raw_tide.get("nextLow") or raw_tide.get("low_next") or ""
            confidence = raw_tide.get("confidence") or raw_tide.get("trust") or "unknown"
            source = raw_tide.get("source") or raw_tide.get("provider") or "unknown"

            forecast_raw = raw_tide.get("forecast") or raw_tide.get("forecasts")
            forecast: Any = {}
            if isinstance(forecast_raw, dict):
                forecast = forecast_raw
            elif isinstance(forecast_raw, list):
                forecast = {"items": forecast_raw}
            else:
                forecast = {}

            return {
                "state": str(state),
                "strength": strength,
                "next_high": str(next_high) if next_high is not None else "",
                "next_low": str(next_low) if next_low is not None else "",
                "confidence": str(confidence),
                "source": str(source),
                "forecast": forecast,
            }
        except Exception as exc:
            _LOGGER.error("Error formatting tide data: %s", exc, exc_info=True)
            return default

    # -----------------
    # Astro
    # -----------------
    @staticmethod
    def format_astro_data(raw_astro: Optional[Dict[str, Any]]) -> AstroData:
        """Normalize astronomical data; keep fields nullable when missing."""
        if not raw_astro or not isinstance(raw_astro, dict):
            return {
                "moon_phase": None,
                "moonrise": None,
                "moonset": None,
                "moon_transit": None,
                "moon_underfoot": None,
                "sunrise": None,
                "sunset": None,
            }

        try:
            mp = _safe_float(raw_astro.get("moon_phase"), None)
            def s(k: str):
                v = raw_astro.get(k)
                return str(v) if v is not None else None

            return {
                "moon_phase": mp,
                "moonrise": s("moonrise") or s("moonRise"),
                "moonset": s("moonset") or s("moonSet"),
                "moon_transit": s("moon_transit") or s("moonTransit"),
                "moon_underfoot": s("moon_underfoot") or s("moonUnderfoot"),
                "sunrise": s("sunrise"),
                "sunset": s("sunset"),
            }
        except Exception as exc:
            _LOGGER.error("Error formatting astro data: %s", exc, exc_info=True)
            return {
                "moon_phase": None,
                "moonrise": None,
                "moonset": None,
                "moon_transit": None,
                "moon_underfoot": None,
                "sunrise": None,
                "sunset": None,
            }

    # -----------------
    # Component scores & scoring results
    # -----------------
    @staticmethod
    def format_component_scores(raw_scores: Optional[Dict[str, Any]]) -> ComponentScores:
        """Normalize component scores into canonical TitleCase keys with float values 0.0..n.

        Unknown keys are ignored (logged at debug).
        """
        canon_map = {
            "season": "Season",
            "temperature": "Temperature",
            "temp": "Temperature",
            "wind": "Wind",
            "pressure": "Pressure",
            "tide": "Tide",
            "moon": "Moon",
            "time": "Time",
            "waves": "Waves",
            "safety": "Safety",
            # Allow TitleCase pass-through
            "Season": "Season",
            "Temperature": "Temperature",
            "Wind": "Wind",
            "Pressure": "Pressure",
            "Tide": "Tide",
            "Moon": "Moon",
            "Time": "Time",
            "Waves": "Waves",
            "Safety": "Safety",
        }

        normalized: Dict[str, float] = {
            "Season": 0.0,
            "Temperature": 0.0,
            "Wind": 0.0,
            "Pressure": 0.0,
            "Tide": 0.0,
            "Moon": 0.0,
            "Time": 0.0,
            "Waves": 0.0,
            "Safety": 0.0,
        }

        if not raw_scores or not isinstance(raw_scores, dict):
            return normalized

        for k, v in raw_scores.items():
            if k is None:
                continue
            key_str = str(k)
            key_lower = key_str.lower()
            canon = canon_map.get(key_str) or canon_map.get(key_lower)
            if canon is None:
                # Best-effort Title-case fallback only if it matches an expected key
                fallback = key_str.title()
                if fallback in normalized:
                    canon = fallback
                else:
                    _LOGGER.debug("Unexpected component score key (ignored): %s", key_str)
                    continue
            val = _safe_float(v, 0.0)
            normalized[canon] = 0.0 if val is None else float(val)

        return {
            "Season": normalized["Season"],
            "Temperature": normalized["Temperature"],
            "Wind": normalized["Wind"],
            "Pressure": normalized["Pressure"],
            "Tide": normalized["Tide"],
            "Moon": normalized["Moon"],
            "Time": normalized["Time"],
            "Waves": normalized["Waves"],
            "Safety": normalized["Safety"],
        }

    @staticmethod
    def format_score_result(result: Optional[Dict[str, Any]]) -> ScoringResult:
        """Normalize scoring result structure used by sensors/UI."""
        if not result or not isinstance(result, dict):
            return {
                "score": 0.0,
                "breakdown": {},
                "component_scores": DataFormatter.format_component_scores({}),
                "conditions_summary": "",
            }
        try:
            score_val = _safe_float(result.get("score"), 0.0) or 0.0
            breakdown = result.get("breakdown") or {}
            comp_scores = DataFormatter.format_component_scores(result.get("component_scores") or {})
            cond = str(result.get("conditions_summary") or "")
            return {
                "score": round(float(score_val), 1),
                "breakdown": breakdown,
                "component_scores": comp_scores,
                "conditions_summary": cond,
            }
        except Exception as exc:
            _LOGGER.error("Error formatting scoring result: %s", exc, exc_info=True)
            return {
                "score": 0.0,
                "breakdown": {},
                "component_scores": DataFormatter.format_component_scores({}),
                "conditions_summary": "",
            }

    # -----------------
    # Forecast -> Period/Daily
    # -----------------
    @staticmethod
    def format_period_forecast(
        time_block: str,
        hours: str,
        score: Any,
        component_scores: Optional[Dict[str, Any]],
        weather: Optional[Dict[str, Any]],
        tide_state: str = "n/a",
        safety: str = "safe",
        safety_reasons: Optional[List[str]] = None,
        conditions: str = "",
    ) -> PeriodForecast:
        """Return a normalized PeriodForecast dict."""
        return {
            "time_block": str(time_block or ""),
            "hours": str(hours or ""),
            "score": round(float(_safe_float(score, 0.0) or 0.0), 1),
            "component_scores": DataFormatter.format_component_scores(component_scores or {}),
            "safety": str(safety or "safe"),
            "safety_reasons": safety_reasons or [],
            "tide_state": str(tide_state or "n/a"),
            "conditions": str(conditions or ""),
            "weather": DataFormatter.format_weather_data(weather or {}),
        }

    @staticmethod
    def format_daily_forecast(date: str, day_name: str, periods: Dict[str, Any]) -> DailyForecast:
        """Return canonical DailyForecast. Accepts periods as dict or list."""
        formatted_periods: Dict[str, PeriodForecast] = {}
        total_score = 0.0
        best_period: Optional[str] = None
        best_score = -1.0

        # Accept list/sequence or dict
        if isinstance(periods, list):
            # Turn into dict keyed by index/name
            for idx, pdata in enumerate(periods):
                pname = pdata.get("time_block") if isinstance(pdata, dict) and pdata.get("time_block") else f"period_{idx}"
                formatted_periods[pname] = DataFormatter.format_period_forecast(
                    time_block=pdata.get("time_block", pname) if isinstance(pdata, dict) else pname,
                    hours=pdata.get("hours", "") if isinstance(pdata, dict) else "",
                    score=pdata.get("score", 0.0) if isinstance(pdata, dict) else 0.0,
                    component_scores=pdata.get("component_scores", {}) if isinstance(pdata, dict) else {},
                    weather=pdata.get("weather", {}) if isinstance(pdata, dict) else {},
                    tide_state=pdata.get("tide_state", "n/a") if isinstance(pdata, dict) else "n/a",
                    safety=pdata.get("safety", "safe") if isinstance(pdata, dict) else "safe",
                    safety_reasons=pdata.get("safety_reasons", []) if isinstance(pdata, dict) else [],
                    conditions=pdata.get("conditions", "") if isinstance(pdata, dict) else "",
                )
        elif isinstance(periods, dict):
            for period_name, period_data in periods.items():
                if not isinstance(period_data, dict):
                    # Skip invalid entries
                    continue
                formatted_periods[period_name] = DataFormatter.format_period_forecast(
                    time_block=period_data.get("time_block", period_name),
                    hours=period_data.get("hours", ""),
                    score=period_data.get("score", 0.0),
                    component_scores=period_data.get("component_scores", {}),
                    weather=period_data.get("weather", {}),
                    tide_state=period_data.get("tide_state", "n/a"),
                    safety=period_data.get("safety", "safe"),
                    safety_reasons=period_data.get("safety_reasons", []),
                    conditions=period_data.get("conditions", ""),
                )
        else:
            # Unknown shape -> leave empty periods
            formatted_periods = {}

        for pname, p in formatted_periods.items():
            try:
                ps = float(p.get("score", 0.0) or 0.0)
            except Exception:
                ps = 0.0
            total_score += ps
            if ps > best_score:
                best_score = ps
                best_period = pname

        daily_avg = (total_score / len(formatted_periods)) if formatted_periods else 0.0

        return {
            "date": date,
            "day_name": day_name or DataFormatter._day_name_from_date(date),
            "periods": formatted_periods,
            "daily_avg_score": round(daily_avg, 1),
            "best_period": best_period,
            "best_score": round(best_score if best_score >= 0 else 0.0, 1),
        }

    @staticmethod
    def normalize_forecast(raw_forecast: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Normalize different incoming forecast shapes into:
          { "YYYY-MM-DD": { "day_name": "Monday", "periods": { "day": {...} } } }

        Accepts:
          - date -> metrics (dict)  => converted to a single 'day' period
          - date -> {"day_name", "periods"} => passed through (but validated)
        """
        normalized: Dict[str, Dict[str, Any]] = {}
        if not raw_forecast or not isinstance(raw_forecast, dict):
            return normalized

        for date_str, daily_data in raw_forecast.items():
            # Already normalized-like
            if isinstance(daily_data, dict) and "periods" in daily_data:
                dn = daily_data.get("day_name") or DataFormatter._day_name_from_date(date_str)
                normalized[date_str] = {"day_name": dn, "periods": daily_data.get("periods", {})}
                continue

            # If daily_data looks like a dict of metrics (temperature, wind, etc.), wrap into 'day'
            if isinstance(daily_data, dict):
                dn = DataFormatter._day_name_from_date(date_str)
                periods = {
                    "day": {
                        "time_block": "day",
                        "hours": "00:00-23:59",
                        "score": daily_data.get("score", 0.0) if isinstance(daily_data.get("score"), (int, float)) else 0.0,
                        "component_scores": daily_data.get("component_scores", {}),
                        "weather": daily_data,
                        "tide_state": daily_data.get("tide_state", "n/a"),
                        "safety": daily_data.get("safety", "safe"),
                        "safety_reasons": daily_data.get("safety_reasons", []),
                        "conditions": daily_data.get("conditions", ""),
                    }
                }
                normalized[date_str] = {"day_name": dn, "periods": periods}
                continue

            # Unknown shape: still produce an empty day entry
            dn = DataFormatter._day_name_from_date(date_str)
            normalized[date_str] = {"day_name": dn, "periods": {}}

        return normalized

    @staticmethod
    def _day_name_from_date(date_str: Optional[str]) -> str:
        """Return weekday name from ISO date string, or empty string on failure."""
        if not date_str or not isinstance(date_str, str):
            return ""
        try:
            # Support both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS" by splitting
            date_only = date_str.split("T")[0]
            dt = datetime.fromisoformat(date_only)
            return dt.strftime("%A")
        except Exception:
            return ""

    # -----------------
    # Sensor attributes packaging
    # -----------------
    @staticmethod
    def format_sensor_attributes(
        score: Any,
        conditions: Any,
        component_scores: Optional[Dict[str, Any]],
        weather: Optional[Dict[str, Any]],
        astro: Optional[Dict[str, Any]],
        mode: str,
        species: Optional[List[str]],
        location: str,
        forecast: Optional[Dict[str, Any]] = None,
        marine: Optional[Dict[str, Any]] = None,
        tide: Optional[Dict[str, Any]] = None,
    ) -> SensorAttributes:
        """Return canonical SensorAttributes dict used by the sensor entity.

        - Normalizes forecast shapes.
        - Ensures marine/tide are never None and are normalized.
        - Rounds/cleans main scalar fields.
        """
        # Normalize forecast -> detailed daily forecasts
        formatted_forecast: Dict[str, DailyForecast] = {}
        if forecast and isinstance(forecast, dict):
            try:
                normalized = DataFormatter.normalize_forecast(forecast)
                for date_str, daily_data in normalized.items():
                    try:
                        formatted_forecast[date_str] = DataFormatter.format_daily_forecast(
                            date=date_str,
                            day_name=daily_data.get("day_name", ""),
                            periods=daily_data.get("periods", {}),
                        )
                    except Exception as exc:
                        _LOGGER.debug("Failed to format daily forecast for %s: %s", date_str, exc)
            except Exception as exc:
                _LOGGER.error("Failed to normalize/format forecast: %s", exc, exc_info=True)
                formatted_forecast = {}

        # Ensure marine/tide shapes
        marine_out = DataFormatter.format_marine_data(marine) if marine else {"current": {}, "forecast": {}}
        tide_out = DataFormatter.format_tide_data(tide) if tide else {"state": "unknown", "strength": 0, "next_high": "", "next_low": "", "confidence": "unknown", "source": "unknown", "forecast": {}}

        # Ensure species is a list
        species_list = species if isinstance(species, list) else [species] if species else []

        try:
            score_val = round(float(_safe_float(score, 0.0) or 0.0), 1)
        except Exception:
            score_val = 0.0

        return {
            "score": score_val,
            "conditions": str(conditions or ""),
            "component_scores": DataFormatter.format_component_scores(component_scores or {}),
            "weather": DataFormatter.format_weather_data(weather or {}),
            "marine": marine_out,
            "tide": tide_out,
            "astro": DataFormatter.format_astro_data(astro or {}),
            "forecast": formatted_forecast,
            "mode": str(mode or ""),
            "species": species_list,
            "location": str(location or ""),
            "last_updated": _iso_now_z(),
        }

    # -----------------
    # Validation
    # -----------------
    @staticmethod
    def validate_sensor_attributes(attributes: Dict[str, Any]) -> bool:
        """Basic validation of sensor attributes; returns True if ok.

        This is a light-weight check (not exhaustive schema validation).
        """
        if not attributes or not isinstance(attributes, dict):
            _LOGGER.error("Sensor attributes must be a dict")
            return False

        required_fields = ["score", "conditions", "component_scores", "weather", "mode", "species"]

        for field in required_fields:
            if field not in attributes:
                _LOGGER.error("Missing required field in sensor attributes: %s", field)
                return False

        if not isinstance(attributes.get("score"), (int, float)):
            _LOGGER.error("Score must be numeric")
            return False

        score_val = float(attributes.get("score") or 0.0)
        if score_val < 0 or score_val > 10:
            _LOGGER.error("Score out of range: %s", score_val)
            return False

        if not isinstance(attributes.get("species", []), list):
            _LOGGER.error("Species must be a list")
            return False

        # component_scores must be a dict with numeric values
        cs = attributes.get("component_scores", {})
        if not isinstance(cs, dict):
            _LOGGER.error("component_scores must be a dict")
            return False
        for k, v in cs.items():
            if v is None:
                continue
            if not isinstance(v, (int, float)):
                try:
                    _ = float(v)
                except Exception:
                    _LOGGER.error("component_scores value for %s is not numeric: %s", k, v)
                    return False

        return True