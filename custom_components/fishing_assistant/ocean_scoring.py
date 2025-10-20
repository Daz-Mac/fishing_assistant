"""Ocean fishing scoring algorithm."""
import logging
from datetime import datetime
from typing import Dict, Optional

from .const import (
    CONF_SPECIES_ID,
    CONF_SPECIES_REGION,
    TIDE_STATE_RISING,
    TIDE_STATE_FALLING,
    TIDE_STATE_SLACK_HIGH,
    TIDE_STATE_SLACK_LOW,
    LIGHT_DAWN,
    LIGHT_DAY,
    LIGHT_DUSK,
    LIGHT_NIGHT,
)
from .species_loader import SpeciesLoader

_LOGGER = logging.getLogger(__name__)


class OceanFishingScorer:
    """Calculate ocean fishing scores based on conditions and species."""

    def __init__(self, hass, config: Dict):
        """Initialize the scorer."""
        self.hass = hass
        self.config = config
        self.species_loader = SpeciesLoader(hass)
        
        # Load species profile
        species_id = config.get(CONF_SPECIES_ID, "general_mixed")
        self.species_profile = self.species_loader.get_species(species_id)
        
        if not self.species_profile:
            _LOGGER.warning(
                "Species profile '%s' not found, using fallback", species_id
            )
            self.species_profile = self._get_fallback_profile()

    def _get_fallback_profile(self) -> Dict:
        """Return a fallback species profile."""
        return {
            "id": "general_mixed",
            "name": "General Mixed Species",
            "active_months": list(range(1, 13)),
            "best_tide": "moving",
            "light_preference": "dawn_dusk",
            "cloud_bonus": 0.5,
            "wave_preference": "moderate",
        }

    def calculate_score(
        self,
        tide_state: str,
        tide_strength: float,
        wave_height: float,
        wind_speed: float,
        cloud_cover: float,
        light_condition: str,
        moon_phase: float,
        pressure: float,
        current_time: datetime,
    ) -> Dict:
        """Calculate the fishing score based on all conditions."""
        
        scores = {}
        weights = {
            "tide": 0.25,
            "weather": 0.20,
            "waves": 0.15,
            "light": 0.15,
            "moon": 0.10,
            "season": 0.10,
            "pressure": 0.05,
        }

        # Tide score
        scores["tide"] = self._score_tide(tide_state, tide_strength)

        # Weather score (wind + clouds)
        scores["weather"] = self._score_weather(wind_speed, cloud_cover)

        # Wave score
        scores["waves"] = self._score_waves(wave_height)

        # Light condition score
        scores["light"] = self._score_light(light_condition)

        # Moon phase score
        scores["moon"] = self._score_moon(moon_phase)

        # Seasonal score
        scores["season"] = self._score_season(current_time)

        # Barometric pressure score
        scores["pressure"] = self._score_pressure(pressure)

        # Calculate weighted total
        total_score = sum(scores[key] * weights[key] for key in scores)
        
        # Scale to 0-10
        final_score = round(total_score * 10, 1)

        return {
            "score": final_score,
            "component_scores": scores,
            "weights": weights,
            "species": self.species_profile.get("name", "Unknown"),
        }

    def _score_tide(self, tide_state: str, tide_strength: float) -> float:
        """Score based on tide conditions (0-1)."""
        best_tide = self.species_profile.get("best_tide", "moving")
        
        score = 0.5  # Base score
        
        if best_tide == "any":
            score = 0.8
        elif best_tide == "moving":
            if tide_state in [TIDE_STATE_RISING, TIDE_STATE_FALLING]:
                score = 0.7 + (tide_strength * 0.3)
        elif best_tide == "rising":
            if tide_state == TIDE_STATE_RISING:
                score = 0.8 + (tide_strength * 0.2)
        elif best_tide == "falling":
            if tide_state == TIDE_STATE_FALLING:
                score = 0.8 + (tide_strength * 0.2)
        elif best_tide == "slack":
            if tide_state in [TIDE_STATE_SLACK_HIGH, TIDE_STATE_SLACK_LOW]:
                score = 0.9
        elif best_tide == "slack_high":
            if tide_state == TIDE_STATE_SLACK_HIGH:
                score = 1.0
        elif best_tide == "slack_low":
            if tide_state == TIDE_STATE_SLACK_LOW:
                score = 1.0

        return score

    def _score_weather(self, wind_speed: float, cloud_cover: float) -> float:
        """Score based on weather conditions (0-1)."""
        # Wind score (ideal: 5-15 km/h)
        if wind_speed < 5:
            wind_score = 0.6
        elif wind_speed < 15:
            wind_score = 1.0
        elif wind_speed < 25:
            wind_score = 0.7
        elif wind_speed < 35:
            wind_score = 0.4
        else:
            wind_score = 0.2

        # Cloud score with species preference
        cloud_bonus = self.species_profile.get("cloud_bonus", 0.5)
        cloud_score = 0.5 + (cloud_cover / 100 * cloud_bonus)

        return (wind_score * 0.6) + (cloud_score * 0.4)

    def _score_waves(self, wave_height: float) -> float:
        """Score based on wave conditions (0-1)."""
        wave_pref = self.species_profile.get("wave_preference", "moderate")
        wave_bonus = self.species_profile.get("wave_bonus", False)
        
        if wave_pref == "calm":
            if wave_height < 0.5:
                score = 1.0
            elif wave_height < 1.0:
                score = 0.7
            elif wave_height < 1.5:
                score = 0.4
            else:
                score = 0.2
        elif wave_pref == "moderate":
            if wave_height < 0.5:
                score = 0.6
            elif wave_height < 1.5:
                score = 1.0
            elif wave_height < 2.5:
                score = 0.7
            else:
                score = 0.3
        elif wave_pref == "active":
            if wave_height < 1.0:
                score = 0.5
            elif wave_height < 2.5:
                score = 1.0
            elif wave_height < 3.5:
                score = 0.8
            else:
                score = 0.3
        else:  # any
            score = 0.7

        # Apply wave bonus if species benefits from waves
        if wave_bonus and wave_height > 1.0:
            score = min(1.0, score + 0.2)

        return score

    def _score_light(self, light_condition: str) -> float:
        """Score based on light conditions (0-1)."""
        light_pref = self.species_profile.get("light_preference", "dawn_dusk")
        
        score_map = {
            "day": {LIGHT_DAY: 1.0, LIGHT_DAWN: 0.7, LIGHT_DUSK: 0.7, LIGHT_NIGHT: 0.3},
            "night": {LIGHT_NIGHT: 1.0, LIGHT_DUSK: 0.7, LIGHT_DAWN: 0.6, LIGHT_DAY: 0.2},
            "dawn": {LIGHT_DAWN: 1.0, LIGHT_DAY: 0.7, LIGHT_DUSK: 0.6, LIGHT_NIGHT: 0.4},
            "dusk": {LIGHT_DUSK: 1.0, LIGHT_NIGHT: 0.7, LIGHT_DAWN: 0.6, LIGHT_DAY: 0.4},
            "dawn_dusk": {LIGHT_DAWN: 1.0, LIGHT_DUSK: 1.0, LIGHT_DAY: 0.6, LIGHT_NIGHT: 0.5},
            "low_light": {LIGHT_DAWN: 1.0, LIGHT_DUSK: 1.0, LIGHT_NIGHT: 0.9, LIGHT_DAY: 0.4},
        }
        
        return score_map.get(light_pref, {}).get(light_condition, 0.5)

    def _score_moon(self, moon_phase: float) -> float:
        """Score based on moon phase (0-1)."""
        # New moon (0) and full moon (1) are typically best
        # Quarter moons (0.25, 0.75) are less ideal
        
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 1.0  # New or full moon
        elif 0.4 < moon_phase < 0.6:
            return 0.9  # Around full moon
        elif 0.2 < moon_phase < 0.3 or 0.7 < moon_phase < 0.8:
            return 0.6  # Quarter moons
        else:
            return 0.7  # In between

    def _score_season(self, current_time: datetime) -> float:
        """Score based on seasonal activity (0-1)."""
        current_month = current_time.month
        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        
        if current_month in active_months:
            return 1.0
        else:
            # Check if we're close to active season
            months_to_season = min(
                abs(current_month - m) if abs(current_month - m) <= 6
                else 12 - abs(current_month - m)
                for m in active_months
            )
            
            if months_to_season == 1:
                return 0.6
            elif months_to_season == 2:
                return 0.4
            else:
                return 0.2

    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure (0-1)."""
        # Ideal pressure: 1013-1020 hPa
        # Rising pressure after low is good
        # Falling pressure can trigger feeding
        
        if 1013 <= pressure <= 1020:
            return 1.0
        elif 1008 <= pressure < 1013:
            return 0.8  # Slightly low, often good
        elif 1020 < pressure <= 1025:
            return 0.7  # Slightly high
        elif 1000 <= pressure < 1008:
            return 0.6  # Low pressure
        elif pressure > 1025:
            return 0.5  # High pressure
        else:
            return 0.4  # Very low pressure
