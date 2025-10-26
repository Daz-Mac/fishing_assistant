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
        """Convert raw weather data to standardized WeatherData format.
        
        Args:
            raw_weather: Raw weather data from weather_fetcher or API
            
        Returns:
            Standardized WeatherData dictionary
        """
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
            _LOGGER.error(f"Error formatting weather data: {e}")
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
        """Convert raw marine data to standardized MarineData format.
        
        Args:
            raw_marine: Raw marine data from marine_data.py or None
            
        Returns:
            Standardized MarineData dictionary or None
        """
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
            _LOGGER.error(f"Error formatting marine data: {e}")
            return None

    @staticmethod
    def format_tide_data(raw_tide: Optional[Dict[str, Any]]) -> Optional[TideData]:
        """Convert raw tide data to standardized TideData format.
        
        Args:
            raw_tide: Raw tide data from tide_proxy.py or None
            
        Returns:
            Standardized TideData dictionary or None
        """
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
            _LOGGER.error(f"Error formatting tide data: {e}")
            return None

    @staticmethod
    def format_astro_data(raw_astro: Dict[str, Any]) -> AstroData:
        """Convert raw astronomical data to standardized AstroData format.
        
        Args:
            raw_astro: Raw astro data from helpers/astro.py
            
        Returns:
            Standardized AstroData dictionary
        """
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
            _LOGGER.error(f"Error formatting astro data: {e}")
            return AstroData()

    @staticmethod
    def format_component_scores(raw_scores: Dict[str, float]) -> ComponentScores:
        """Convert raw component scores to standardized ComponentScores format.
        
        Args:
            raw_scores: Raw scores dictionary from scoring modules
            
        Returns:
            Standardized ComponentScores dictionary
        """
        return ComponentScores(
            Season=raw_scores.get("Season", 0.0),
            Temperature=raw_scores.get("Temperature", 0.0),
            Wind=raw_scores.get("Wind", 0.0),
            Pressure=raw_scores.get("Pressure", 0.0),
            Tide=raw_scores.get("Tide", 0.0),
            Moon=raw_scores.get("Moon", 0.0),
            Time=raw_scores.get("Time", 0.0),
            Waves=raw_scores.get("Waves", 0.0),
            Safety=raw_scores.get("Safety", 0.0),
        )

    @staticmethod
    def format_score_result(result: Dict[str, Any]) -> ScoringResult:
        """Convert raw scoring results to standardized ScoringResult format.
        
        Args:
            result: Raw result from scorer calculate_score()
            
        Returns:
            Standardized ScoringResult dictionary
        """
        return ScoringResult(
            score=round(result.get("score", 0.0), 1),
            breakdown=result.get("breakdown", {}),
            component_scores=DataFormatter.format_component_scores(
                result.get("component_scores", {})
            ),
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
        """Convert raw period forecast data to standardized PeriodForecast format.
        
        Args:
            time_block: Period name (morning, afternoon, etc.)
            hours: Time range string (HH:MM-HH:MM)
            score: Fishing score for this period
            component_scores: Component scores breakdown
            weather: Weather data for this period
            tide_state: Tide state during this period
            safety: Safety level (safe, caution, unsafe)
            safety_reasons: List of safety concerns
            conditions: Human-readable conditions summary
            
        Returns:
            Standardized PeriodForecast dictionary
        """
        return PeriodForecast(
            time_block=time_block,
            hours=hours,
            score=round(score, 1),
            component_scores=DataFormatter.format_component_scores(component_scores),
            safety=safety,
            safety_reasons=safety_reasons or [],
            tide_state=tide_state,
            conditions=conditions,
            weather=DataFormatter.format_weather_data(weather),
        )

    @staticmethod
    def format_daily_forecast(
        date: str,
        day_name: str,
        periods: Dict[str, Dict[str, Any]],
    ) -> DailyForecast:
        """Convert raw daily forecast data to standardized DailyForecast format.
        
        Args:
            date: ISO date string (YYYY-MM-DD)
            day_name: Day of week name
            periods: Dictionary of period forecasts
            
        Returns:
            Standardized DailyForecast dictionary
        """
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
            
            period_score = formatted_period["score"]
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
        """Convert raw sensor data to standardized SensorAttributes format.
        
        This is the main function to format all sensor attributes for Home Assistant.
        
        Args:
            score: Overall fishing score
            conditions: Conditions summary
            component_scores: Component scores breakdown
            weather: Current weather data
            astro: Astronomical data
            mode: Fishing mode (freshwater or ocean)
            species: List of target species
            location: Location name
            forecast: Optional forecast data
            marine: Optional marine data (ocean mode)
            tide: Optional tide data
            
        Returns:
            Standardized SensorAttributes dictionary
        """
        formatted_forecast = {}
        if forecast:
            for date_str, daily_data in forecast.items():
                formatted_forecast[date_str] = DataFormatter.format_daily_forecast(
                    date=date_str,
                    day_name=daily_data.get("day_name", ""),
                    periods=daily_data.get("periods", {}),
                )
        
        return SensorAttributes(
            score=round(score, 1),
            conditions=conditions,
            component_scores=DataFormatter.format_component_scores(component_scores),
            weather=DataFormatter.format_weather_data(weather),
            marine=DataFormatter.format_marine_data(marine),
            tide=DataFormatter.format_tide_data(tide),
            astro=DataFormatter.format_astro_data(astro),
            forecast=formatted_forecast,
            mode=mode,
            species=species,
            location=location,
            last_updated=datetime.now().isoformat(),
        )

    @staticmethod
    def validate_sensor_attributes(attributes: SensorAttributes) -> bool:
        """Validate that sensor attributes contain required fields.
        
        Args:
            attributes: SensorAttributes to validate
            
        Returns:
            True if valid, False otherwise
        """
        required_fields = ["score", "conditions", "component_scores", "weather", "mode", "species"]
        
        for field in required_fields:
            if field not in attributes:
                _LOGGER.error(f"Missing required field in sensor attributes: {field}")
                return False
        
        if not isinstance(attributes["score"], (int, float)):
            _LOGGER.error("Score must be a number")
            return False
        
        if attributes["score"] < 0 or attributes["score"] > 10:
            _LOGGER.error(f"Score out of range: {attributes['score']}")
            return False
        
        return True