"""Base scorer abstract class for Fishing Assistant.

This module provides the abstract base class that both freshwater and ocean
scoring modules inherit from, ensuring consistent structure and interface.
The file has been hardened for defensive handling of missing/incorrect types
and to ensure outputs are numeric and stable for downstream consumers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging
import math

from .data_schema import (
    WeatherData,
    MarineData,
    TideData,
    AstroData,
    ComponentScores,
    ScoringResult,
)
from .data_formatter import DataFormatter

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
        self.species = species or []
        self.species_profiles = species_profiles or {}
        # Internal storage of last run results
        self._component_scores: ComponentScores = {}
        self._conditions_summary: str = ""

        _LOGGER.debug(
            "Initialized %s for species: %s at (%.6f, %.6f)",
            self.__class__.__name__,
            self.species,
            float(self.latitude or 0.0),
            float(self.longitude or 0.0),
        )

    @abstractmethod
    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> ComponentScores:
        """Calculate component scores.

        Args:
            weather_data: Raw weather data dictionary
            astro_data: Raw astronomical data dictionary
            tide_data: Optional raw tide data dictionary
            marine_data: Optional raw marine data dictionary
            current_time: Optional datetime object for time-based scoring

        Returns:
            ComponentScores dictionary with individual component scores
        """
        raise NotImplementedError

    @abstractmethod
    def _get_factor_weights(self) -> Dict[str, float]:
        """Get the weights for each scoring factor.

        Returns:
            Dictionary mapping factor names to their weights
        """
        raise NotImplementedError

    def calculate_score(
        self,
        weather_data: Optional[Dict[str, Any]],
        astro_data: Optional[Dict[str, Any]],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> ScoringResult:
        """Calculate the fishing score based on conditions.

        This wraps the concrete _calculate_base_score, ensures component scores
        are numeric and normalized, computes a weighted final score, stores a
        human-readable summary, logs details, and returns the formatted result.

        Args:
            weather_data: Raw weather data dictionary
            astro_data: Raw astronomical data dictionary
            tide_data: Optional raw tide data dictionary
            marine_data: Optional raw marine data dictionary
            current_time: Optional datetime object for time-based scoring

        Returns:
            ScoringResult with score, breakdown, and component scores
        """
        # Ensure inputs are not None
        weather_data = weather_data or {}
        astro_data = astro_data or {}
        try:
            # Calculate component scores (implementations will format data internally)
            raw_component_scores = self._calculate_base_score(
                weather_data, astro_data, tide_data, marine_data, current_time
            ) or {}

            # Ensure component_scores are numeric and within 0-10
            component_scores: ComponentScores = {}
            for k, v in (raw_component_scores.items() if isinstance(raw_component_scores, dict) else []):
                try:
                    val = float(v)
                    if math.isnan(val) or math.isinf(val):
                        raise ValueError("Non-finite component score")
                except Exception:
                    _LOGGER.debug("Non-numeric component score for %s: %r — defaulting to 5.0", k, v)
                    val = 5.0
                # Clamp to 0-10
                component_scores[k] = max(0.0, min(10.0, val))

            # Format weather for summary (we still need it here). Use DataFormatter defensively.
            weather = DataFormatter.format_weather_data(weather_data or {})

            # Get weights and calculate weighted average
            weights = self._get_factor_weights() or {}
            final_score = self._weighted_average(component_scores, weights)
            # Ensure final_score is numeric and in range
            try:
                final_score = float(final_score)
                if math.isnan(final_score) or math.isinf(final_score):
                    raise ValueError("Non-finite final score")
            except Exception:
                _LOGGER.warning("Final score produced non-numeric value (%r). Defaulting to 5.0", final_score)
                final_score = 5.0
            final_score = max(0.0, min(10.0, final_score))

            # Store for later retrieval
            self._component_scores = component_scores
            self._conditions_summary = self._format_conditions_text(final_score, weather, component_scores)

            # Log details
            self._log_scoring_details(final_score, component_scores)

            # Return formatted result using DataFormatter to ensure consistent external shape
            return DataFormatter.format_score_result(
                {
                    "score": round(final_score, 1),
                    "conditions_summary": self._conditions_summary,
                    "component_scores": component_scores,
                    "breakdown": {},
                }
            )

        except Exception as exc:
            _LOGGER.exception("Unhandled error while calculating score: %s", exc)
            # Return a safe default structured result
            return DataFormatter.format_score_result(
                {
                    "score": 5.0,
                    "conditions_summary": "Error calculating score",
                    "component_scores": {},
                    "breakdown": {},
                }
            )

    @abstractmethod
    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature.

        Args:
            temperature: Temperature in Celsius

        Returns:
            Score from 0-10
        """
        raise NotImplementedError

    @abstractmethod
    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        """Score based on wind conditions.

        Args:
            wind_speed: Wind speed in km/h
            wind_gust: Wind gust speed in km/h

        Returns:
            Score from 0-10
        """
        raise NotImplementedError

    @abstractmethod
    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure.

        Args:
            pressure: Pressure in hPa

        Returns:
            Score from 0-10
        """
        raise NotImplementedError

    @abstractmethod
    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase.

        Args:
            moon_phase: Moon phase from 0-1 (0=new, 0.5=full)

        Returns:
            Score from 0-10
        """
        raise NotImplementedError

    @abstractmethod
    def _score_time_of_day(self, current_time: Any, astro: Dict[str, Any]) -> float:
        """Score based on time of day.

        Args:
            current_time: Current datetime object
            astro: Astronomical data dictionary

        Returns:
            Score from 0-10
        """
        raise NotImplementedError

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
        try:
            s = float(score)
        except Exception:
            s = 5.0
        if math.isnan(s) or math.isinf(s):
            s = 5.0
        return max(0.0, min(10.0, s))

    def _weighted_average(self, scores: Dict[str, float], weights: Dict[str, float]) -> float:
        """Calculate weighted average of scores.

        Args:
            scores: Dictionary of component scores
            weights: Dictionary of weights for each component

        Returns:
            Weighted average score
        """
        if not isinstance(weights, dict) or not weights:
            # If no weights provided, compute simple average of available scores
            vals = [float(v) for v in scores.values() if self._is_finite_number(v)]
            if not vals:
                return 5.0
            return sum(vals) / len(vals)

        total_weight = 0.0
        weighted_sum = 0.0
        for key, weight in weights.items():
            try:
                w = float(weight)
            except Exception:
                _LOGGER.debug("Non-numeric weight for %s: %r — skipping", key, weight)
                continue
            if w <= 0:
                continue
            total_weight += w
            raw_val = scores.get(key, 5.0)
            try:
                val = float(raw_val)
                if not self._is_finite_number(val):
                    raise ValueError
            except Exception:
                _LOGGER.debug("Non-numeric score for %s: %r — using 5.0", key, raw_val)
                val = 5.0
            weighted_sum += val * w

        if total_weight <= 0:
            return 5.0

        return weighted_sum / total_weight

    @staticmethod
    def _is_finite_number(v: Any) -> bool:
        """Return True if v is a finite numeric value."""
        try:
            f = float(v)
            return not (math.isinf(f) or math.isnan(f))
        except Exception:
            return False

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
            profile = self.species_profiles.get(species_name, {}) or {}

            # Support multiple naming shapes for temperature ranges
            tr = None
            if "temperature_range" in profile:
                tr = profile["temperature_range"]
            elif "temp_range" in profile:
                tr = profile["temp_range"]
            elif "temp_min" in profile and "temp_max" in profile:
                tr = {"min": profile.get("temp_min"), "max": profile.get("temp_max")}
            if tr:
                temp_ranges.append(tr)

            if "activity_pattern" in profile:
                activity_patterns.append(profile["activity_pattern"])

        aggregated: Dict[str, Any] = {}

        # Average temperature ranges if possible
        if temp_ranges:
            mins = []
            maxs = []
            opt_mins = []
            opt_maxs = []
            for r in temp_ranges:
                # r may be dict-like or list/tuple
                try:
                    if isinstance(r, dict):
                        mins.append(float(r.get("min", 0)))
                        maxs.append(float(r.get("max", 30)))
                        opt_mins.append(float(r.get("optimal_min", mins[-1] if mins else 15)))
                        opt_maxs.append(float(r.get("optimal_max", maxs[-1] if maxs else 25)))
                    elif isinstance(r, (list, tuple)) and len(r) >= 2:
                        mins.append(float(r[0]))
                        maxs.append(float(r[1]))
                        # best-effort fill
                        span = float(maxs[-1] - mins[-1]) if maxs and mins else 25.0
                        opt_mins.append(mins[-1] + span * 0.2)
                        opt_maxs.append(maxs[-1] - span * 0.2)
                except Exception:
                    _LOGGER.debug("Unable to parse temperature range entry: %r", r)

            if mins and maxs:
                aggregated["temp_min"] = sum(mins) / len(mins)
                aggregated["temp_max"] = sum(maxs) / len(maxs)
                aggregated["temp_optimal_min"] = (
                    sum(opt_mins) / len(opt_mins) if opt_mins else aggregated["temp_min"] + 2
                )
                aggregated["temp_optimal_max"] = (
                    sum(opt_maxs) / len(opt_maxs) if opt_maxs else aggregated["temp_max"] - 2
                )

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
        try:
            if score >= 8:
                rating = "Excellent"
            elif score >= 6:
                rating = "Good"
            elif score >= 4:
                rating = "Fair"
            else:
                rating = "Poor"

            # Find best and worst factors
            scores_dict = dict(component_scores or {})
            # Ensure numeric comparison
            safe_scores = {k: (float(v) if self._is_finite_number(v) else 5.0) for k, v in scores_dict.items()}
            best_factor = max(safe_scores, key=safe_scores.get) if safe_scores else "Unknown"
            worst_factor = min(safe_scores, key=safe_scores.get) if safe_scores else "Unknown"

            # Safe weather values for formatting
            def safe_weather_float(key: str, default: float = 0.0) -> float:
                try:
                    val = weather.get(key, default)
                    return float(val) if self._is_finite_number(val) else default
                except Exception:
                    return default

            temp_val = safe_weather_float("temperature", 0.0)
            wind_val = safe_weather_float("wind_speed", 0.0)

            summary = f"{rating} conditions. "
            summary += f"Best: {best_factor} ({safe_scores.get(best_factor, 0.0):.1f}/10). "
            summary += f"Worst: {worst_factor} ({safe_scores.get(worst_factor, 0.0):.1f}/10). "
            summary += f"Temp: {temp_val:.1f}°C, "
            summary += f"Wind: {wind_val:.1f} km/h"

            return summary
        except Exception:
            _LOGGER.exception("Error formatting conditions summary")
            return "Conditions summary unavailable."

    def _log_scoring_details(self, score: float, component_scores: ComponentScores):
        """Log detailed scoring information for debugging.

        Args:
            score: Overall fishing score
            component_scores: Component scores breakdown
        """
        try:
            _LOGGER.debug("Final Score: %.1f/10", float(score))
            _LOGGER.debug("Component Scores:")
            for component, component_score in (component_scores or {}).items():
                try:
                    _LOGGER.debug("  %s: %.1f/10", component, float(component_score))
                except Exception:
                    _LOGGER.debug("  %s: %r (non-numeric)", component, component_score)
        except Exception:
            _LOGGER.exception("Failed to log scoring details")