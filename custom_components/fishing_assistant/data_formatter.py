"""Data formatting and integration layer for Fishing Assistant.

This module provides a DataFormatter class to convert raw API data into standardized
formats defined in data_schema.py, ensuring consistency across the integration.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
import logging

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


class DataFormatter:
    """Data formatter for converting raw API data to standardized formats."""

    @staticmethod
    def format_weather_data(raw_weather: Dict[str, Any]) -> WeatherData:
        """Convert raw weather data to standardized WeatherData format."""
        try:
            return WeatherData(
                temperature=float(raw_weather.get("temperature", 0)),
                wind_speed=float(raw_weather.get("wind_speed", 0)),
                wind_gust=float(raw_weather.get("wind_gust", raw_weather.get("wind_speed", 0))),
                cloud_cover=float(raw_weather.get("cloud_cover", 0)),
                precipitation_probability=float(raw_weather.get("precipitation_probability", 0)),
                pressure=float(raw_weather.get("pressure", 1013.25)),
                datetime=raw_weather.get("datetime", datetime.now().isoformat()),
            )
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error formatting weather data: %s", e, exc_info=True)
            return WeatherData(
                temperature=15.0,
                wind_speed=0.0,
                wind_gust=0.0,
                cloud_cover=50.0,
                precipitation_probability=0.0,
                pressure=1013.25,
                datetime=datetime.now().isoformat(),
            )

    @staticmethod
    def format_marine_data(raw_marine: Optional[Dict[str, Any]]) -> Optional[MarineData]:
        """Convert raw marine data to standardized MarineData format."""
        if not raw_marine:
            return None

        try:
            return MarineData(
                wave_height=raw_marine.get("wave_height"),
                wave_period=raw_marine.get("wave_period"),
                wave_direction=raw_marine.get("wave_direction"),
                wind_wave_height=raw_marine.get("wind_wave_height"),
                wind_wave_period=raw_marine.get("wind_wave_period"),
                swell_wave_height=raw_marine.get("swell_wave_height"),
                swell_wave_period=raw_marine.get("swell_wave_period"),
                timestamp=raw_marine.get("timestamp", datetime.now().isoformat()),
            )
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error formatting marine data: %s", e, exc_info=True)
            return None

    @staticmethod
    def format_tide_data(raw_tide: Optional[Dict[str, Any]]) -> Optional[TideData]:
        """Convert raw tide data to standardized TideData format."""
        if not raw_tide:
            return None

        try:
            return TideData(
                state=raw_tide.get("state", "unknown"),
                strength=int(raw_tide.get("strength", 0)),
                next_high=raw_tide.get("next_high", ""),
                next_low=raw_tide.get("next_low", ""),
                confidence=raw_tide.get("confidence", "unknown"),
                source=raw_tide.get("source", "unknown"),
            )
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error formatting tide data: %s", e, exc_info=True)
            return None

    @staticmethod
    def format_astro_data(raw_astro: Dict[str, Any]) -> AstroData:
        """Convert raw astronomical data to standardized AstroData format."""
        try:
            return AstroData(
                moon_phase=raw_astro.get("moon_phase"),
                moonrise=raw_astro.get("moonrise"),
                moonset=raw_astro.get("moonset"),
                moon_transit=raw_astro.get("moon_transit"),
                moon_underfoot=raw_astro.get("moon_underfoot"),
                sunrise=raw_astro.get("sunrise"),
                sunset=raw_astro.get("sunset"),
            )
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error formatting astro data: %s", e, exc_info=True)
            return AstroData()

    @staticmethod
    def format_component_scores(raw_scores: Dict[str, float]) -> ComponentScores:
        """
        Convert raw component scores to standardized ComponentScores format.

        This function is tolerant of varying key names returned by scorers. It maps
        common lowercase or variant keys to the canonical TitleCase names expected by
        ComponentScores.
        """
        # Map many possible incoming keys to canonical TitleCase keys used by ComponentScores
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
            # Accept the canonical TitleCase keys as-is too
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

        # Start with zeros for all expected keys
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
            return ComponentScores(**normalized)

        # Normalize keys and coerce to float where possible
        for k, v in raw_scores.items():
            if k is None:
                continue
            key_str = str(k)
            key_lower = key_str.lower()
            canon = canon_map.get(key_str, canon_map.get(key_lower))
            if canon is None:
                # Unknown key: try Title-case of the incoming key (best-effort)
                canon = key_str[0].upper() + key_str[1:] if key_str else key_str
            # Convert value safely
            try:
                normalized[canon] = float(v) if v is not None else 0.0
            except Exception:
                _LOGGER.debug("Unable to convert component score %s=%s to float", k, v)
                normalized[canon] = 0.0

        # Log if there were keys not expected (debug)
        extra_keys = set([k for k in raw_scores.keys() if (k not in canon_map and k.capitalize() not in normalized)])
        if extra_keys:
            _LOGGER.debug("format_component_scores received unexpected keys: %s", extra_keys)

        return ComponentScores(
            Season=normalized["Season"],
            Temperature=normalized["Temperature"],
            Wind=normalized["Wind"],
            Pressure=normalized["Pressure"],
            Tide=normalized["Tide"],
            Moon=normalized["Moon"],
            Time=normalized["Time"],
            Waves=normalized["Waves"],
            Safety=normalized["Safety"],
        )

    @staticmethod
    def format_score_result(result: Dict[str, Any]) -> ScoringResult:
        """Convert raw scoring results to standardized ScoringResult format."""
        return ScoringResult(
            score=round(result.get("score", 0.0), 1),
            breakdown=result.get("breakdown", {}),
            component_scores=DataFormatter.format_component_scores(result.get("component_scores", {})),
            conditions_summary=result.get("conditions_summary", ""),
        )

    @staticmethod
    def format_period_forecast(
        time_block: str,
        hours: str,
        score: float,
        component_scores: Dict[str, float],
        weather: Dict[str, Any],
        tide_state: str = "n/a",
        safety: str = "safe",
        safety_reasons: Optional[List[str]] = None,
        conditions: str = "",
    ) -> PeriodForecast:
        """Convert raw period forecast data to standardized PeriodForecast format."""
        return PeriodForecast(
            time_block=time_block,
            hours=hours,
            score=round(score, 1),
            component_scores=DataFormatter.format_component_scores(component_scores or {}),
            safety=safety,
            safety_reasons=safety_reasons or [],
            tide_state=tide_state,
            conditions=conditions,
            weather=DataFormatter.format_weather_data(weather or {}),
        )

    @staticmethod
    def format_daily_forecast(
        date: str,
        day_name: str,
        periods: Dict[str, Dict[str, Any]],
    ) -> DailyForecast:
        """Convert raw daily forecast data to standardized DailyForecast format."""
        formatted_periods = {}
        total_score = 0.0
        best_period = None
        best_score = 0.0

        for period_name, period_data in periods.items():
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

            # Accessing typed dict / dict style
            try:
                period_score = formatted_period["score"]
            except Exception:
                # If typed object uses attribute access, fallback
                period_score = getattr(formatted_period, "score", 0.0)

            total_score += period_score

            if period_score > best_score:
                best_score = period_score
                best_period = period_name

        daily_avg = total_score / len(periods) if periods else 0.0

        return DailyForecast(
            date=date,
            day_name=day_name,
            periods=formatted_periods,
            daily_avg_score=round(daily_avg, 1),
            best_period=best_period,
            best_score=round(best_score, 1),
        )

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
            # If already looks normalized, pass through
            if isinstance(daily_data, dict) and "periods" in daily_data:
                # Ensure day_name exists
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
                        "score": 0.0,  # scorer will fill or be computed elsewhere
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
            # Accept full ISO datetimes or date-only strings
            date_only = date_str.split("T")[0]
            dt = datetime.fromisoformat(date_only)
            return dt.strftime("%A")
        except Exception:
            return ""

    @staticmethod
    def format_sensor_attributes(
        score: float,
        conditions: str,
        component_scores: Dict[str, float],
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
        formatted_forecast = {}
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

        return SensorAttributes(
            score=round(score, 1),
            conditions=conditions,
            component_scores=DataFormatter.format_component_scores(component_scores or {}),
            weather=DataFormatter.format_weather_data(weather or {}),
            marine=DataFormatter.format_marine_data(marine),
            tide=DataFormatter.format_tide_data(tide),
            astro=DataFormatter.format_astro_data(astro or {}),
            forecast=formatted_forecast,
            mode=mode,
            species=species,
            location=location,
            last_updated=datetime.now().isoformat(),
        )

    @staticmethod
    def validate_sensor_attributes(attributes: SensorAttributes) -> bool:
        """Validate that sensor attributes contain required fields."""
        required_fields = ["score", "conditions", "component_scores", "weather", "mode", "species"]

        for field in required_fields:
            if field not in attributes:
                _LOGGER.error("Missing required field in sensor attributes: %s", field)
                return False

        if not isinstance(attributes["score"], (int, float)):
            _LOGGER.error("Score must be a number")
            return False

        if attributes["score"] < 0 or attributes["score"] > 10:
            _LOGGER.error("Score out of range: %s", attributes["score"])
            return False

        return True