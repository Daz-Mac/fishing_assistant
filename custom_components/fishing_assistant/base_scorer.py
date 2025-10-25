"""Base scorer abstract class for Fishing Assistant.

This module provides the abstract base class that both freshwater and ocean
scoring modules inherit from, ensuring consistent structure and interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging

from .data_schema import (
    WeatherData,
    MarineData,
    TideData,
    AstroData,
    ComponentScores,
    ScoringResult,
)
from .data_formatter import (
    format_weather_data,
    format_marine_data,
    format_tide_data,
    format_astro_data,
    format_scoring_result,
)

_LOGGER = logging.getLogger(__name__)


class BaseScorer(ABC):
    """Abstract base class for fishing condition scoring.
    
    All scoring modules (freshwater, ocean) must inherit from this class
    and implement the required abstract methods.
    """
    
    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: Dict[str, Any],
    ):
        """Initialize the base scorer.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
            species: List of target species names
            species_profiles: Dictionary of species profile data
        """
        self.latitude = latitude
        self.longitude = longitude
        self.species = species
        self.species_profiles = species_profiles
        self._component_scores: ComponentScores = {}
        self._conditions_summary: str = ""
        
        _LOGGER.debug(
            f"Initialized {self.__class__.__name__} for species: {species} "
            f"at ({latitude}, {longitude})"
        )
    
    @abstractmethod
    def _calculate_base_score(
        self,
        weather: WeatherData,
        astro: AstroData,
        tide: Optional[TideData] = None,
        marine: Optional[MarineData] = None,
        current_time: Optional[Any] = None,
    ) -> ComponentScores:
        """Calculate component scores.
        
        Args:
            weather: Formatted weather data
            astro: Formatted astronomical data
            tide: Optional formatted tide data
            marine: Optional formatted marine data
            current_time: Optional datetime object for time-based scoring
            
        Returns:
            ComponentScores dictionary with individual component scores
        """
        pass
    
    @abstractmethod
    def _get_factor_weights(self) -> Dict[str, float]:
        """Get the weights for each scoring factor.
        
        Returns:
            Dictionary mapping factor names to their weights
        """
        pass
    
    def calculate_score(
        self,
        weather: Dict[str, Any],
        astro: Dict[str, Any],
        tide: Optional[Dict[str, Any]] = None,
        marine: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> ScoringResult:
        """Calculate the fishing score based on conditions.
        
        Args:
            weather: Raw weather data dictionary
            astro: Raw astronomical data dictionary
            tide: Optional raw tide data dictionary
            marine: Optional raw marine data dictionary
            current_time: Optional datetime object for time-based scoring
            
        Returns:
            ScoringResult with score, breakdown, and component scores
        """
        # Format input data
        weather_data = format_weather_data(weather)
        astro_data = format_astro_data(astro)
        tide_data = format_tide_data(tide) if tide else None
        marine_data = format_marine_data(marine) if marine else None
        
        # Calculate component scores
        component_scores = self._calculate_base_score(
            weather_data, astro_data, tide_data, marine_data, current_time
        )
        
        # Get weights and calculate weighted average
        weights = self._get_factor_weights()
        final_score = self._weighted_average(component_scores, weights)
        
        # Store for later retrieval
        self._component_scores = component_scores
        self._conditions_summary = self._format_conditions_text(
            final_score, weather_data, component_scores
        )
        
        # Log details
        self._log_scoring_details(final_score, component_scores)
        
        # Return formatted result
        return format_scoring_result(
            final_score, self._conditions_summary, component_scores
        )
    
    @abstractmethod
    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature.
        
        Args:
            temperature: Temperature in Celsius
            
        Returns:
            Score from 0-10
        """
        pass
    
    @abstractmethod
    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        """Score based on wind conditions.
        
        Args:
            wind_speed: Wind speed in km/h
            wind_gust: Wind gust speed in km/h
            
        Returns:
            Score from 0-10
        """
        pass
    
    @abstractmethod
    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure.
        
        Args:
            pressure: Pressure in hPa
            
        Returns:
            Score from 0-10
        """
        pass
    
    @abstractmethod
    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase.
        
        Args:
            moon_phase: Moon phase from 0-1 (0=new, 0.5=full)
            
        Returns:
            Score from 0-10
        """
        pass
    
    @abstractmethod
    def _score_time_of_day(self, current_time: Any, astro: Dict[str, Any]) -> float:
        """Score based on time of day.
        
        Args:
            current_time: Current datetime object
            astro: Astronomical data dictionary
            
        Returns:
            Score from 0-10
        """
        pass
    
    def get_component_scores(self) -> ComponentScores:
        """Get the component scores from the last calculation.
        
        Returns:
            ComponentScores dictionary with individual component scores
        """
        return self._component_scores
    
    def get_conditions_summary(self) -> str:
        """Get the conditions summary from the last calculation.
        
        Returns:
            Human-readable conditions summary string
        """
        return self._conditions_summary
    
    def _normalize_score(self, score: float) -> float:
        """Normalize a score to 0-10 range.
        
        Args:
            score: Raw score value
            
        Returns:
            Normalized score between 0 and 10
        """
        return max(0.0, min(10.0, score))
    
    def _weighted_average(self, scores: Dict[str, float], weights: Dict[str, float]) -> float:
        """Calculate weighted average of scores.
        
        Args:
            scores: Dictionary of component scores
            weights: Dictionary of weights for each component
            
        Returns:
            Weighted average score
        """
        total_weight = sum(weights.values())
        if total_weight == 0:
            return 0.0
        
        weighted_sum = sum(scores.get(key, 0) * weight for key, weight in weights.items())
        return weighted_sum / total_weight
    
    def _get_species_preferences(self) -> Dict[str, Any]:
        """Get aggregated preferences for all target species.
        
        Returns:
            Dictionary with aggregated species preferences
        """
        if not self.species or not self.species_profiles:
            return {}
        
        # Aggregate preferences across all species
        temp_ranges = []
        activity_patterns = []
        
        for species_name in self.species:
            profile = self.species_profiles.get(species_name, {})
            
            if "temperature_range" in profile:
                temp_ranges.append(profile["temperature_range"])
            
            if "activity_pattern" in profile:
                activity_patterns.append(profile["activity_pattern"])
        
        aggregated = {}
        
        # Average temperature ranges
        if temp_ranges:
            aggregated["temp_min"] = sum(r.get("min", 0) for r in temp_ranges) / len(temp_ranges)
            aggregated["temp_max"] = sum(r.get("max", 30) for r in temp_ranges) / len(temp_ranges)
            aggregated["temp_optimal_min"] = sum(r.get("optimal_min", 15) for r in temp_ranges) / len(temp_ranges)
            aggregated["temp_optimal_max"] = sum(r.get("optimal_max", 25) for r in temp_ranges) / len(temp_ranges)
        
        # Combine activity patterns
        if activity_patterns:
            aggregated["activity_patterns"] = activity_patterns
        
        return aggregated
    
    def _format_conditions_text(
        self,
        score: float,
        weather: WeatherData,
        component_scores: ComponentScores,
    ) -> str:
        """Generate human-readable conditions summary.
        
        Args:
            score: Overall fishing score
            weather: Formatted weather data
            component_scores: Component scores breakdown
            
        Returns:
            Human-readable conditions summary
        """
        if score >= 8:
            rating = "Excellent"
        elif score >= 6:
            rating = "Good"
        elif score >= 4:
            rating = "Fair"
        else:
            rating = "Poor"
        
        # Find best and worst factors
        scores_dict = dict(component_scores)
        best_factor = max(scores_dict, key=scores_dict.get) if scores_dict else "Unknown"
        worst_factor = min(scores_dict, key=scores_dict.get) if scores_dict else "Unknown"
        
        summary = f"{rating} conditions. "
        summary += f"Best: {best_factor} ({scores_dict.get(best_factor, 0):.1f}/10). "
        summary += f"Worst: {worst_factor} ({scores_dict.get(worst_factor, 0):.1f}/10). "
        summary += f"Temp: {weather.get('temperature', 0):.1f}°C, "
        summary += f"Wind: {weather.get('wind_speed', 0):.1f} km/h"
        
        return summary
    
    def _log_scoring_details(self, score: float, component_scores: ComponentScores):
        """Log detailed scoring information for debugging.
        
        Args:
            score: Overall fishing score
            component_scores: Component scores breakdown
        """
        _LOGGER.debug(f"Final Score: {score:.1f}/10")
        _LOGGER.debug("Component Scores:")
        for component, component_score in component_scores.items():
            _LOGGER.debug(f"  {component}: {component_score:.1f}/10")