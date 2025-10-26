"""Freshwater fishing scoring algorithm with period-based forecasting."""
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any

from .base_scorer import BaseScorer
from .const import TIME_PERIODS_FULL_DAY
from .species_loader import SpeciesLoader
from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)


class FreshwaterFishingScorer(BaseScorer):
    """Freshwater fishing scoring implementation."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: dict[str, Any],
        body_type: str = None,
        species_loader: SpeciesLoader = None,
    ):
        """Initialize the freshwater scorer.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
            species: List of species IDs
            species_profiles: Dictionary of species profiles
            body_type: Type of water body (lake, river, etc.)
            species_loader: Species loader instance
        """
        super().__init__(latitude, longitude, species, species_profiles)
        
        self.species_name = species[0] if species else "general"
        self.body_type = body_type or "lake"
        self.species_loader = species_loader
        
        # Get species profile
        if self.species_name in species_profiles:
            self.species_profile = species_profiles[self.species_name]
        elif species_loader:
            self.species_profile = species_loader.get_species(self.species_name)
            if self.species_profile:
                self.species_profiles[self.species_name] = self.species_profile
        else:
            self.species_profile = {}
        
        if not self.species_profile:
            _LOGGER.warning(f"Species profile not found: {self.species_name}")
            self.species_profile = {}

    def calculate_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Calculate the fishing score with error handling and forecast support.
        
        Args:
            weather_data: Raw weather data dictionary
            astro_data: Raw astronomical data dictionary
            tide_data: Optional raw tide data dictionary
            marine_data: Optional raw marine data dictionary
            current_time: Optional datetime object for time-based scoring
            
        Returns:
            ScoringResult with score, breakdown, component scores, and forecast
        """
        try:
            # Call parent calculate_score
            result = super().calculate_score(
                weather_data, astro_data, tide_data, marine_data, current_time
            )
            
            # Add forecast if available in weather_data
            if "forecast" in weather_data and isinstance(weather_data["forecast"], list):
                result["forecast"] = self._format_forecast(weather_data["forecast"], astro_data)
            
            return result
            
        except Exception as e:
            _LOGGER.error(f"Error calculating freshwater score: {e}", exc_info=True)
            # Return default result
            return DataFormatter.format_score_result({
                "score": 5.0,
                "conditions_summary": "Error calculating score",
                "component_scores": {},
                "breakdown": {}
            })

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Calculate component scores with error handling.
        
        Args:
            weather_data: Raw weather data dictionary
            astro_data: Raw astronomical data dictionary
            tide_data: Optional raw tide data dictionary (not used for freshwater)
            marine_data: Optional raw marine data dictionary (not used for freshwater)
            current_time: Optional datetime object for time-based scoring
            
        Returns:
            Dictionary of component scores
        """
        try:
            # Format input data
            weather = DataFormatter.format_weather_data(weather_data)
            astro = DataFormatter.format_astro_data(astro_data)
            
            if current_time is None:
                current_time = datetime.now()
            
            components = {}
            
            # Temperature Score
            temp = weather.get("temperature")
            if temp is not None:
                components["temperature"] = self._normalize_score(self._score_temperature(temp))
            else:
                components["temperature"] = 5.0
            
            # Wind Score
            wind_speed = weather.get("wind_speed", 0)
            wind_gust = weather.get("wind_gust", wind_speed)
            components["wind"] = self._normalize_score(self._score_wind(wind_speed, wind_gust))
            
            # Pressure Score
            pressure = weather.get("pressure", 1013)
            components["pressure"] = self._normalize_score(self._score_pressure(pressure))
            
            # Cloud Cover Score
            cloud_cover = weather.get("cloud_cover", 50)
            components["clouds"] = self._normalize_score(self._score_cloud_cover(cloud_cover))
            
            # Time of Day Score
            components["time"] = self._normalize_score(self._score_time_of_day(current_time, astro))
            
            # Season Score
            components["season"] = self._normalize_score(self._score_season(current_time))
            
            # Moon Phase Score
            moon_phase = astro.get("moon_phase")
            components["moon"] = self._normalize_score(self._score_moon(moon_phase))
            
            return components
            
        except Exception as e:
            _LOGGER.error(f"Error in _calculate_base_score: {e}", exc_info=True)
            # Return default scores
            return {
                "temperature": 5.0,
                "wind": 5.0,
                "pressure": 5.0,
                "clouds": 5.0,
                "time": 5.0,
                "season": 5.0,
                "moon": 5.0,
            }

    def _get_factor_weights(self) -> Dict[str, float]:
        """Get factor weights for scoring.
        
        Returns:
            Dictionary of factor weights
        """
        return {
            "temperature": 0.25,
            "wind": 0.15,
            "pressure": 0.15,
            "clouds": 0.15,
            "time": 0.15,
            "season": 0.10,
            "moon": 0.05,
        }

    def _format_forecast(self, forecast_data: List[Dict[str, Any]], astro_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format forecast data for frontend compatibility.
        
        Args:
            forecast_data: List of forecast entries
            astro_data: Astronomical data for moon phase
            
        Returns:
            List of formatted forecast entries
        """
        formatted_forecast = []
        
        for entry in forecast_data:
            try:
                # Calculate score for this forecast entry
                forecast_weather = {
                    "temperature": entry.get("temperature"),
                    "wind_speed": entry.get("wind_speed"),
                    "wind_gust": entry.get("wind_gust", entry.get("wind_speed", 0) * 1.5),
                    "pressure": entry.get("pressure"),
                    "precipitation": entry.get("precipitation", 0),
                    "cloud_cover": entry.get("cloud_cover", 0),
                    "humidity": entry.get("humidity", 50),
                }
                
                # Calculate component scores
                component_scores = self._calculate_base_score(
                    forecast_weather, astro_data, None, None, entry.get("datetime")
                )
                
                # Calculate final score
                weights = self._get_factor_weights()
                score = self._weighted_average(component_scores, weights)
                
                # Normalize component scores for frontend (0-100)
                normalized_scores = {
                    key: round(value * 10, 1) for key, value in component_scores.items()
                }
                
                formatted_forecast.append({
                    "datetime": entry.get("datetime"),
                    "score": round(score, 1),
                    "temperature": entry.get("temperature"),
                    "wind_speed": entry.get("wind_speed"),
                    "pressure": entry.get("pressure"),
                    "component_scores": normalized_scores,
                })
            except Exception as e:
                _LOGGER.warning(f"Error formatting forecast entry: {e}")
                continue
        
        return formatted_forecast

    async def calculate_forecast(
        self,
        weather_forecast: List[Dict[str, Any]],
        tide_forecast: Optional[List[Dict[str, Any]]] = None,
        marine_forecast: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Calculate fishing scores for forecast periods.
        
        Args:
            weather_forecast: List of weather forecast data
            tide_forecast: Optional list of tide forecast data (not used for freshwater)
            marine_forecast: Optional list of marine forecast data (not used for freshwater)
            
        Returns:
            List of forecast scores with timestamps
        """
        forecast_scores = []
        
        for weather_data in weather_forecast:
            try:
                # Get timestamp from weather data
                forecast_time = weather_data.get("datetime")
                if not forecast_time:
                    continue
                
                # Get astro data for this time
                astro_data = weather_data.get("astro", {})
                
                # Calculate score
                score_result = self.calculate_score(
                    weather_data=weather_data,
                    astro_data=astro_data,
                    tide_data=None,
                    marine_data=None,
                    current_time=forecast_time
                )
                
                # Add timestamp to result
                score_result["datetime"] = forecast_time
                forecast_scores.append(score_result)
                
            except Exception as e:
                _LOGGER.error("Error calculating forecast score: %s", e, exc_info=True)
                continue
        
        return forecast_scores

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