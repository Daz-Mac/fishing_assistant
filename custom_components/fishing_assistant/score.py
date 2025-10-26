"""Freshwater fishing scoring algorithm with period-based forecasting."""
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any

from .base_scorer import BaseScorer
from .const import TIME_PERIODS_FULL_DAY
from .species_loader import SpeciesLoader

_LOGGER = logging.getLogger(__name__)


class FreshwaterFishingScorer(BaseScorer):
    """Freshwater fishing scoring implementation."""

    def __init__(
        self,
        species_name: str,
        body_type: str,
        species_loader: SpeciesLoader,
        latitude: float = 0.0,
        longitude: float = 0.0,
    ):
        """Initialize the freshwater scorer.
        
        Args:
            species_name: Name of the target species
            body_type: Type of water body (lake, river, etc.)
            species_loader: Species loader instance
            latitude: Location latitude
            longitude: Location longitude
        """
        self.species_name = species_name
        self.body_type = body_type
        self.species_loader = species_loader
        
        # Get species profile
        self.species_profile = species_loader.get_species(species_name)
        if not self.species_profile:
            _LOGGER.warning(f"Species profile not found: {species_name}")
            self.species_profile = {}
        
        # Initialize parent with required parameters
        species_profiles = {species_name: self.species_profile}
        super().__init__(
            latitude=latitude,
            longitude=longitude,
            species=[species_name],
            species_profiles=species_profiles
        )

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Calculate component scores.
        
        Args:
            weather_data: Formatted weather data
            astro_data: Formatted astronomical data
            tide_data: Optional formatted tide data (not used for freshwater)
            marine_data: Optional formatted marine data (not used for freshwater)
            current_time: Optional datetime object for time-based scoring
            
        Returns:
            Dictionary of component scores
        """
        if current_time is None:
            current_time = datetime.now()
        
        components = {}
        
        # Temperature Score
        temp = weather_data.get("temperature")
        if temp is not None:
            components["temperature"] = self._score_temperature(temp)
        else:
            components["temperature"] = 5.0
        
        # Wind Score
        wind_speed = weather_data.get("wind_speed", 0)
        wind_gust = weather_data.get("wind_gust", wind_speed)
        components["wind"] = self._score_wind(wind_speed, wind_gust)
        
        # Pressure Score
        pressure = weather_data.get("pressure", 1013)
        components["pressure"] = self._score_pressure(pressure)
        
        # Cloud Cover Score
        cloud_cover = weather_data.get("cloud_cover", 50)
        components["cloud_cover"] = self._score_cloud_cover(cloud_cover)
        
        # Time of Day Score
        components["time_of_day"] = self._score_time_of_day(current_time, astro_data)
        
        # Season Score
        components["season"] = self._score_season(current_time)
        
        # Moon Phase Score
        moon_phase = astro_data.get("moon_phase")
        components["moon"] = self._score_moon(moon_phase)
        
        return components

    def _get_factor_weights(self) -> Dict[str, float]:
        """Get factor weights for scoring.
        
        Returns:
            Dictionary of factor weights
        """
        return {
            "temperature": 0.25,
            "wind": 0.15,
            "pressure": 0.15,
            "cloud_cover": 0.15,
            "time_of_day": 0.15,
            "season": 0.10,
            "moon": 0.05,
        }

    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature."""
        temp_range = self.species_profile.get("temp_range", [5, 30])
        if len(temp_range) == 2:
            min_temp, max_temp = temp_range
            temp_span = max_temp - min_temp
            optimal_min = min_temp + (temp_span * 0.2)
            optimal_max = max_temp - (temp_span * 0.2)

            if optimal_min <= temperature <= optimal_max:
                return 10.0
            elif min_temp <= temperature <= max_temp:
                return 7.0
            else:
                if temperature < min_temp:
                    distance = min_temp - temperature
                else:
                    distance = temperature - max_temp
                return max(2.0, 7.0 - (distance * 0.5))
        return 5.0

    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        """Score based on wind conditions."""
        if 5 <= wind_speed <= 15:
            return 10.0
        elif wind_speed > 25:
            return 3.0
        else:
            return 7.0

    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure."""
        prefers_low = self.species_profile.get("prefers_low_pressure", False)
        
        if prefers_low:
            if pressure < 1010:
                return 10.0
            elif pressure < 1015:
                return 8.0
            else:
                return 5.0
        else:
            if 1013 <= pressure <= 1020:
                return 10.0
            elif 1010 <= pressure <= 1025:
                return 7.0
            else:
                return 4.0

    def _score_cloud_cover(self, cloud_cover: float) -> float:
        """Score based on cloud cover."""
        ideal_cloud = self.species_profile.get("ideal_cloud", 50)
        cloud_diff = abs(cloud_cover - ideal_cloud)
        
        if cloud_diff <= 15:
            return 10.0
        elif cloud_diff <= 30:
            return 7.0
        else:
            return 4.0

    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase."""
        if moon_phase is None:
            return 5.0
        
        # New moon and full moon are typically better
        if moon_phase < 0.1 or moon_phase > 0.9:  # New moon
            return 9.0
        elif 0.4 < moon_phase < 0.6:  # Full moon
            return 9.0
        else:
            return 6.0

    def _score_time_of_day(self, current_time: datetime, astro: Dict[str, Any]) -> float:
        """Score based on time of day."""
        hour = current_time.hour
        # Dawn and dusk are best
        if 5 <= hour <= 8 or 17 <= hour <= 20:
            return 10.0
        else:
            return 6.0

    def _score_season(self, current_time: datetime) -> float:
        """Score based on season/active months."""
        current_month = current_time.month
        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        
        if current_month in active_months:
            return 10.0
        else:
            return 3.0