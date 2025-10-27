"""Data formatting and integration layer for Fishing Assistant.

This module provides a DataFormatter class to convert raw API data into standardized
formats defined in data_schema.py, ensuring consistency across the integration.

Note: data_schema types are TypedDicts (i.e. dicts at runtime). This module returns
plain dicts that conform to those TypedDict shapes so they can be JSON-serialized
and used easily by Home Assistant components.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import logging
import math

from .data_schema import (
    WeatherData,
    MarineData,
    TideData,
    AstroData,
    ComponentScores,
    ScoringResult,
    PeriodForecast,
    DailyForecast,
    SensorAttributes,
)

_LOGGER = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> Optional[float]:
    """Safely convert val to float; return default (or None) on failure."""
    if val is None:
        return None if default is None else default
    try:
        if isinstance(val, bool):
            # bool is subclass of int; preserve as numeric but cast to float
            return float(val)
        if isinstance(val, (int, float)):
            # Normalize NaN to None
            if isinstance(val, float) and math.isnan(val):
                return None
            return float(val)
        s = str(val).strip()
        if s == "":
            return None if default is None else default
        f = float(s)
        if math.isnan(f):
            return None
        return f
    except Exception:
        return default


def _iso_now_z() -> str:
    """Return current UTC time as ISO string with Z suffix (e.g. 2025-10-27T12:34:56Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DataFormatter:
    """Data formatter for converting raw API data to standardized formats."""

    @staticmethod
    def format_weather_data(raw_weather: Dict[str, Any]) -> WeatherData:
        """Convert raw weather data to standardized WeatherData format.

        Returns a dict matching the WeatherData TypedDict.
        """
        try:
            # Accept several common key names used by different providers
            temp = raw_weather.get("temperature") or raw_weather.get("temperature_2m") or raw_weather.get("temp")
            wind = raw_weather.get("wind_speed") or raw_weather.get("wind_speed_10m") or raw_weather.get("wind")
            wind_gust = raw_weather.get("wind_gust") or raw_weather.get("wind_speed") or wind
            cloud = raw_weather.get("cloud_cover") or raw_weather.get("cloudcover") or raw_weather.get("clouds")
            precip = raw_weather.get("precipitation_probability") or raw_weather.get("precipitation")
            pressure = raw_weather.get("pressure") or raw_weather.get("pressure_msl") or 1013.25
            dt = raw_weather.get("datetime") or raw_weather.get("time") or _iso_now_z()

            return {
                "temperature": _safe_float(temp, 0.0),
                "wind_speed": _safe_float(wind, 0.0),
                "wind_gust": _safe_float(wind_gust, _safe_float(wind, 0.0) or 0.0),
                "cloud_cover": _safe_float(cloud, 0.0),
                "precipitation_probability": _safe_float(precip, 0.0),
                "pressure": _safe_float(pressure, 1013.25),
                "datetime": str(dt),
            }
        except Exception as e:
            _LOGGER.error("Error formatting weather data: %s", e, exc_info=True)
            return {
                "temperature": 15.0,
                "wind_speed": 0.0,
                "wind_gust": 0.0,
                "cloud_cover": 50.0,
                "precipitation_probability": 0.0,
                "pressure": 1013.25,
                "datetime": _iso_now_z(),
            }

    @staticmethod
    def format_marine_data(raw_marine: Optional[Dict[str, Any]]) -> Optional[MarineData]:
        """Convert raw marine data to standardized MarineData format.

        Returns None if input is falsy.
        """
        if not raw_marine:
            return None

        try:
            return {
                "wave_height": _safe_float(raw_marine.get("wave_height")),
                "wave_period": _safe_float(raw_marine.get("wave_period")),
                "wave_direction": _safe_float(raw_marine.get("wave_direction")),
                "wind_wave_height": _safe_float(raw_marine.get("wind_wave_height")),
                "wind_wave_period": _safe_float(raw_marine.get("wind_wave_period")),
                "swell_wave_height": _safe_float(raw_marine.get("swell_wave_height")),
                "swell_wave_period": _safe_float(raw_marine.get("swell_wave_period")),
                "timestamp": str(raw_marine.get("timestamp") or raw_marine.get("time") or _iso_now_z()),
            }
        except Exception as e:
            _LOGGER.error("Error formatting marine data: %s", e, exc_info=True)
            return None

    @staticmethod
    def format_tide_data(raw_tide: Optional[Dict[str, Any]]) -> Optional[TideData]:
        """Convert raw tide data to standardized TideData format."""
        if not raw_tide:
            return None

        try:
            strength_val = raw_tide.get("strength", 0)
            try:
                strength_int = int(strength_val)
            except Exception:
                strength_int = 0

            return {
                "state": str(raw_tide.get("state", "unknown")),
                "strength": strength_int,
                "next_high": str(raw_tide.get("next_high", "")),
                "next_low": str(raw_tide.get("next_low", "")),
                "confidence": str(raw_tide.get("confidence", "unknown")),
                "source": str(raw_tide.get("source", "unknown")),
            }
        except Exception as e:
            _LOGGER.error("Error formatting tide data: %s", e, exc_info=True)
            return None

    @staticmethod
    def format_astro_data(raw_astro: Dict[str, Any]) -> AstroData:
        """Convert raw astronomical data to standardized AstroData format."""
        try:
            return {
                "moon_phase": _safe_float(raw_astro.get("moon_phase"), None),
                "moonrise": str(raw_astro.get("moonrise")) if raw_astro.get("moonrise") is not None else None,
                "moonset": str(raw_astro.get("moonset")) if raw_astro.get("moonset") is not None else None,
                "moon_transit": str(raw_astro.get("moon_transit")) if raw_astro.get("moon_transit") is not None else None,
                "moon_underfoot": str(raw_astro.get("moon_underfoot")) if raw_astro.get("moon_underfoot") is not None else None,
                "sunrise": str(raw_astro.get("sunrise")) if raw_astro.get("sunrise") is not None else None,
                "sunset": str(raw_astro.get("sunset")) if raw_astro.get("sunset") is not None else None,
            }
        except Exception as e:
            _LOGGER.error("Error formatting astro data: %s", e, exc_info=True)
            return {}

    @staticmethod
    def format_component_scores(raw_scores: Dict[str, Any]) -> ComponentScores:
        """
        Convert raw component scores to standardized ComponentScores format.

        Tolerant of variant key names returned by scorers. Returns a dict mapping canonical
        TitleCase keys to float values (0.0 default).
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
            # Accept TitleCase keys as-is too
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

        if not raw_scores:
            return normalized

        for k, v in raw_scores.items():
            if k is None:
                continue
            key_str = str(k)
            key_lower = key_str.lower()
            canon = canon_map.get(key_str) or canon_map.get(key_lower)
            if canon is None:
                # Best-effort Title-case fallback
                canon = key_str[0].upper() + key_str[1:] if key_str else key_str
                if canon not in normalized:
                    # If it's still unexpected, just skip it but log at debug level
                    _LOGGER.debug("Unexpected component score key (ignored): %s", key_str)
                    continue
            try:
                val = _safe_float(v, 0.0)
                normalized[canon] = 0.0 if val is None else float(val)
            except Exception:
                _LOGGER.debug("Unable to convert component score %s=%s to float", k, v)
                normalized[canon] = 0.0

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
    def format_score_result(result: Dict[str, Any]) -> ScoringResult:
        """Convert raw scoring results to standardized ScoringResult format."""
        try:
            score_val = _safe_float(result.get("score"), 0.0) or 0.0
            breakdown = result.get("breakdown", {}) or {}
            component_scores = DataFormatter.format_component_scores(result.get("component_scores", {}) or {})
            conditions_summary = str(result.get("conditions_summary", "") or "")
            return {
                "score": round(float(score_val), 1),
                "breakdown": breakdown,
                "component_scores": component_scores,
                "conditions_summary": conditions_summary,
            }
        except Exception as e:
            _LOGGER.error("Error formatting scoring result: %s", e, exc_info=True)
            return {"score": 0.0, "breakdown": {}, "component_scores": DataFormatter.format_component_scores({}), "conditions_summary": ""}

    @staticmethod
    def format_period_forecast(
        time_block: str,
        hours: str,
        score: float,
        component_scores: Dict[str, Any],
        weather: Dict[str, Any],
        tide_state: str = "n/a",
        safety: str = "safe",
        safety_reasons: Optional[List[str]] = None,
        conditions: str = "",
    ) -> PeriodForecast:
        """Convert raw period forecast data to standardized PeriodForecast format."""
        return {
            "time_block": time_block,
            "hours": hours,
            "score": round(float(_safe_float(score, 0.0)), 1),
            "component_scores": DataFormatter.format_component_scores(component_scores or {}),
            "safety": safety,
            "safety_reasons": safety_reasons or [],
            "tide_state": tide_state,
            "conditions": conditions or "",
            "weather": DataFormatter.format_weather_data(weather or {}),
        }

    @staticmethod
    def format_daily_forecast(
        date: str,
        day_name: str,
        periods: Dict[str, Dict[str, Any]],
    ) -> DailyForecast:
        """Convert raw daily forecast data to standardized DailyForecast format."""
        formatted_periods: Dict[str, PeriodForecast] = {}
        total_score = 0.0
        best_period = None
        best_score = -1.0

        for period_name, period_data in (periods or {}).items():
            formatted_period = DataFormatter.format_period_forecast(
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
            formatted_periods[period_name] = formatted_period

            period_score = formatted_period.get("score", 0.0)
            total_score += period_score

            if period_score > best_score:
                best_score = period_score
                best_period = period_name

        daily_avg = (total_score / len(formatted_periods)) if formatted_periods else 0.0

        return {
            "date": date,
            "day_name": day_name,
            "periods": formatted_periods,
            "daily_avg_score": round(daily_avg, 1),
            "best_period": best_period,
            "best_score": round(best_score if best_score >= 0 else 0.0, 1),
        }

    @staticmethod
    def normalize_forecast(raw_forecast: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Normalize incoming forecast shapes to the internal expected shape:
          { "YYYY-MM-DD": { "day_name": "Monday", "periods": { "day": {...}, "morning": {...} } } }

        Accepts two common shapes:
          - date -> weather metrics (temperature, wind_speed, etc.)  (from WeatherFetcher)
          - date -> { "day_name":..., "periods": {...} } (already normalized)
        """
        normalized: Dict[str, Dict[str, Any]] = {}
        if not raw_forecast:
            return normalized

        for date_str, daily_data in raw_forecast.items():
            # If already looks normalized, pass through (ensure day_name exists)
            if isinstance(daily_data, dict) and "periods" in daily_data:
                dn = daily_data.get("day_name") or DataFormatter._day_name_from_date(date_str)
                normalized[date_str] = {"day_name": dn, "periods": daily_data.get("periods", {})}
                continue

            # If daily_data looks like raw weather metrics, create a single 'day' period
            if isinstance(daily_data, dict):
                dn = DataFormatter._day_name_from_date(date_str)
                periods = {
                    "day": {
                        "time_block": "day",
                        "hours": "00:00-23:59",
                        "score": 0.0,
                        "component_scores": {},
                        "weather": daily_data,
                        "tide_state": daily_data.get("tide_state", "n/a"),
                        "safety": daily_data.get("safety", "safe"),
                        "safety_reasons": daily_data.get("safety_reasons", []),
                        "conditions": daily_data.get("conditions", ""),
                    }
                }
                normalized[date_str] = {"day_name": dn, "periods": periods}
                continue

            # Unknown format: set an empty day so downstream code can handle gracefully
            dn = DataFormatter._day_name_from_date(date_str)
            normalized[date_str] = {"day_name": dn, "periods": {}}

        return normalized

    @staticmethod
    def _day_name_from_date(date_str: str) -> str:
        """Return weekday name from ISO date string, or empty string on failure."""
        try:
            date_only = date_str.split("T")[0]
            dt = datetime.fromisoformat(date_only)
            return dt.strftime("%A")
        except Exception:
            return ""

    @staticmethod
    def format_sensor_attributes(
        score: float,
        conditions: str,
        component_scores: Dict[str, Any],
        weather: Dict[str, Any],
        astro: Dict[str, Any],
        mode: str,
        species: List[str],
        location: str,
        forecast: Optional[Dict[str, Dict[str, Any]]] = None,
        marine: Optional[Dict[str, Any]] = None,
        tide: Optional[Dict[str, Any]] = None,
    ) -> SensorAttributes:
        """
        Convert raw sensor data to standardized SensorAttributes format.

        The function is tolerant of different forecast shapes: if a simple weather-like
        forecast is provided (date -> metrics) it will be normalized into the
        day->periods structure expected by the rest of the integration.
        """
        formatted_forecast: Dict[str, DailyForecast] = {}
        if forecast:
            try:
                normalized = DataFormatter.normalize_forecast(forecast)
                for date_str, daily_data in normalized.items():
                    formatted_forecast[date_str] = DataFormatter.format_daily_forecast(
                        date=date_str,
                        day_name=daily_data.get("day_name", ""),
                        periods=daily_data.get("periods", {}),
                    )
            except Exception as exc:
                _LOGGER.error("Failed to normalize/format forecast: %s", exc, exc_info=True)
                formatted_forecast = {}

        return {
            "score": round(float(_safe_float(score, 0.0)), 1),
            "conditions": str(conditions or ""),
            "component_scores": DataFormatter.format_component_scores(component_scores or {}),
            "weather": DataFormatter.format_weather_data(weather or {}),
            "marine": DataFormatter.format_marine_data(marine),
            "tide": DataFormatter.format_tide_data(tide),
            "astro": DataFormatter.format_astro_data(astro or {}),
            "forecast": formatted_forecast,
            "mode": mode,
            "species": species,
            "location": location,
            "last_updated": _iso_now_z(),
        }

    @staticmethod
    def validate_sensor_attributes(attributes: Dict[str, Any]) -> bool:
        """Validate that sensor attributes contain required fields.

        Accepts a dict matching SensorAttributes (TypedDict) and verifies required keys
        and basic value ranges.
        """
        required_fields = ["score", "conditions", "component_scores", "weather", "mode", "species"]

        for field in required_fields:
            if field not in attributes:
                _LOGGER.error("Missing required field in sensor attributes: %s", field)
                return False

        if not isinstance(attributes.get("score"), (int, float)):
            _LOGGER.error("Score must be a number")
            return False

        score_val = float(attributes.get("score"))
        if score_val < 0 or score_val > 10:
            _LOGGER.error("Score out of range: %s", attributes.get("score"))
            return False

        # Additional quick sanity checks
        if not isinstance(attributes.get("species", []), list):
            _LOGGER.error("Species must be a list")
            return False

        return True