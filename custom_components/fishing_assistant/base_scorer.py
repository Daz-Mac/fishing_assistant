"""Base scorer abstract class for Fishing Assistant.

This module provides the abstract base class that freshwater and ocean
scoring modules inherit from. It contains defensive handling of missing or
incorrect types and ensures outputs are numeric and stable for downstream
consumers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
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

    Concrete scorers should implement the abstract methods to compute the
    individual component scores. The BaseScorer wraps these implementations
    to normalize outputs, compute weighted totals, and provide human-readable
    summaries and logging.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: Dict[str, Any],
    ) -> None:
        """Initialize base scorer.

        Args:
            latitude: Location latitude
            longitude: Location longitude
            species: List of target species names
            species_profiles: Dictionary of species profile data
        """
        self.latitude = float(latitude or 0.0)
        self.longitude = float(longitude or 0.0)
        self.species = list(species or [])
        self.species_profiles = species_profiles or {}
        self._component_scores: ComponentScores = {}
        self._conditions_summary: str = ""

        _LOGGER.debug(
            "Initialized %s for species: %s at (%.6f, %.6f)",
            self.__class__.__name__,
            ", ".join(self.species) if self.species else "<none>",
            self.latitude,
            self.longitude,
        )

    # ----------------------------
    # Abstract methods to override
    # ----------------------------
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

        Implementations should return a dict mapping component name -> numeric score
        (preferably between 0 and 10). This BaseScorer will coerce and clamp values.
        """
        raise NotImplementedError

    @abstractmethod
    def _get_factor_weights(self) -> Dict[str, float]:
        """Return weights for each scoring factor (component_name -> weight)."""
        raise NotImplementedError

    @abstractmethod
    def _score_temperature(self, temperature: float) -> float:
        raise NotImplementedError

    @abstractmethod
    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        raise NotImplementedError

    @abstractmethod
    def _score_pressure(self, pressure: float) -> float:
        raise NotImplementedError

    @abstractmethod
    def _score_moon(self, moon_phase: Optional[float]) -> float:
        raise NotImplementedError

    @abstractmethod
    def _score_time_of_day(self, current_time: Any, astro: Dict[str, Any]) -> float:
        raise NotImplementedError

    # ----------------------------
    # Public API
    # ----------------------------
    def calculate_score(
        self,
        weather_data: Optional[Dict[str, Any]],
        astro_data: Optional[Dict[str, Any]],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> ScoringResult:
        """Calculate the fishing score based on provided inputs.

        Normalizes the component scores, computes a weighted average, stores a
        human-readable summary, logs details and returns a formatted ScoringResult.
        """
        weather_data = weather_data or {}
        astro_data = astro_data or {}

        try:
            raw_component_scores = self._calculate_base_score(
                weather_data, astro_data, tide_data, marine_data, current_time
            ) or {}

            # Ensure component_scores is a dict
            if not isinstance(raw_component_scores, dict):
                _LOGGER.debug("Base scorer returned non-dict component scores: %r", type(raw_component_scores))
                raw_component_scores = {}

            # Coerce component scores to finite floats and clamp 0-10
            component_scores: ComponentScores = {}
            for k, v in raw_component_scores.items():
                try:
                    val = float(v)
                    if math.isnan(val) or math.isinf(val):
                        raise ValueError("Non-finite")
                except Exception:
                    _LOGGER.debug("Invalid component score for '%s': %r — defaulting to 5.0", k, v)
                    val = 5.0
                component_scores[k] = max(0.0, min(10.0, val))

            # Format weather snapshot for summary
            weather = DataFormatter.format_weather_data(weather_data or {})

            # Compute final weighted score
            weights = self._get_factor_weights() or {}
            final_score = self._weighted_average(component_scores, weights)

            # Ensure final is finite and clamped
            try:
                final_score = float(final_score)
                if math.isnan(final_score) or math.isinf(final_score):
                    raise ValueError("Non-finite final score")
            except Exception:
                _LOGGER.warning("Final score non-numeric (%r), defaulting to 5.0", final_score)
                final_score = 5.0
            final_score = max(0.0, min(10.0, final_score))

            # Persist for later retrieval
            self._component_scores = dict(component_scores)
            self._conditions_summary = self._format_conditions_text(final_score, weather, component_scores)

            self._log_scoring_details(final_score, component_scores)

            result: Dict[str, Any] = {
                "score": round(final_score, 1),
                "conditions_summary": self._conditions_summary,
                "component_scores": component_scores,
                "breakdown": {},
            }

            return DataFormatter.format_score_result(result)

        except Exception as exc:
            _LOGGER.exception("Unhandled error while calculating score: %s", exc)
            return DataFormatter.format_score_result(
                {
                    "score": 5.0,
                    "conditions_summary": "Error calculating score",
                    "component_scores": {},
                    "breakdown": {},
                }
            )

    def get_component_scores(self) -> ComponentScores:
        """Return a copy of the last-calculated component scores."""
        return dict(self._component_scores or {})

    def get_conditions_summary(self) -> str:
        """Return the last-calculated human-readable conditions summary."""
        return str(self._conditions_summary or "")

    # ----------------------------
    # Helpers
    # ----------------------------
    def _normalize_score(self, score: Any) -> float:
        """Coerce a value into a finite 0-10 float."""
        try:
            s = float(score)
        except Exception:
            s = 5.0
        if math.isnan(s) or math.isinf(s):
            s = 5.0
        return max(0.0, min(10.0, s))

    def _weighted_average(self, scores: Dict[str, float], weights: Dict[str, float]) -> float:
        """Compute a weighted average of component scores.

        If weights is empty or invalid, compute simple average of available scores.
        """
        if not isinstance(weights, dict) or not weights:
            vals = [float(v) for v in scores.values() if self._is_finite_number(v)]
            if not vals:
                return 5.0
            return sum(vals) / len(vals)

        total_weight = 0.0
        weighted_sum = 0.0
        for key, w_raw in weights.items():
            try:
                w = float(w_raw)
            except Exception:
                _LOGGER.debug("Invalid weight for '%s': %r — skipping", key, w_raw)
                continue
            if w <= 0.0:
                continue
            total_weight += w
            raw_val = scores.get(key, 5.0)
            try:
                val = float(raw_val)
                if math.isnan(val) or math.isinf(val):
                    raise ValueError
            except Exception:
                _LOGGER.debug("Invalid score for weighted key '%s': %r — using 5.0", key, raw_val)
                val = 5.0
            weighted_sum += val * w

        if total_weight <= 0.0:
            return 5.0

        return weighted_sum / total_weight

    @staticmethod
    def _is_finite_number(v: Any) -> bool:
        """Return True if v can be coerced to a finite float."""
        try:
            f = float(v)
            return not (math.isinf(f) or math.isnan(f))
        except Exception:
            return False

    def _get_species_preferences(self) -> Dict[str, Any]:
        """Aggregate preferences across requested species.

        Returns a dictionary with averaged temperature ranges and collected activity patterns.
        """
        if not self.species or not self.species_profiles:
            return {}

        temp_ranges = []
        activity_patterns = []

        for species_name in self.species:
            profile = (self.species_profiles.get(species_name) or {}) or {}

            # Handle several naming schemes for temperature ranges
            tr = None
            if isinstance(profile.get("temperature_range"), (dict, list, tuple)):
                tr = profile.get("temperature_range")
            elif isinstance(profile.get("temp_range"), (dict, list, tuple)):
                tr = profile.get("temp_range")
            elif "temp_min" in profile and "temp_max" in profile:
                tr = {"min": profile.get("temp_min"), "max": profile.get("temp_max")}
            if tr:
                temp_ranges.append(tr)

            ap = profile.get("activity_pattern")
            if ap:
                activity_patterns.append(ap)

        aggregated: Dict[str, Any] = {}

        if temp_ranges:
            mins = []
            maxs = []
            opt_mins = []
            opt_maxs = []
            for r in temp_ranges:
                try:
                    if isinstance(r, dict):
                        mins.append(float(r.get("min", 0.0)))
                        maxs.append(float(r.get("max", 30.0)))
                        opt_mins.append(float(r.get("optimal_min", mins[-1] if mins else (mins[-1] + 2 if mins else 15.0))))
                        opt_maxs.append(float(r.get("optimal_max", maxs[-1] if maxs else (maxs[-1] - 2 if maxs else 25.0))))
                    elif isinstance(r, (list, tuple)) and len(r) >= 2:
                        mins.append(float(r[0]))
                        maxs.append(float(r[1]))
                        span = float(maxs[-1] - mins[-1]) if (maxs and mins) else 25.0
                        opt_mins.append(mins[-1] + span * 0.2)
                        opt_maxs.append(maxs[-1] - span * 0.2)
                except Exception:
                    _LOGGER.debug("Unable to parse temperature range entry: %r", r)

            if mins and maxs:
                aggregated["temp_min"] = sum(mins) / len(mins)
                aggregated["temp_max"] = sum(maxs) / len(maxs)
                aggregated["temp_optimal_min"] = sum(opt_mins) / len(opt_mins) if opt_mins else (aggregated["temp_min"] + 2.0)
                aggregated["temp_optimal_max"] = sum(opt_maxs) / len(opt_maxs) if opt_maxs else (aggregated["temp_max"] - 2.0)

        if activity_patterns:
            aggregated["activity_patterns"] = activity_patterns

        return aggregated

    def _format_conditions_text(
        self,
        score: float,
        weather: WeatherData,
        component_scores: ComponentScores,
    ) -> str:
        """Generate a brief human-readable summary of conditions."""
        try:
            if score >= 8.0:
                rating = "Excellent"
            elif score >= 6.0:
                rating = "Good"
            elif score >= 4.0:
                rating = "Fair"
            else:
                rating = "Poor"

            scores_dict = dict(component_scores or {})
            safe_scores = {k: (float(v) if self._is_finite_number(v) else 5.0) for k, v in scores_dict.items()}
            best_factor = max(safe_scores, key=safe_scores.get) if safe_scores else "Unknown"
            worst_factor = min(safe_scores, key=safe_scores.get) if safe_scores else "Unknown"

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
            summary += f"Temp: {temp_val:.1f}°C, Wind: {wind_val:.1f} km/h"

            return summary
        except Exception:
            _LOGGER.exception("Error formatting conditions summary")
            return "Conditions summary unavailable."

    def _log_scoring_details(self, score: float, component_scores: ComponentScores) -> None:
        """Log detailed scoring information for debugging."""
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