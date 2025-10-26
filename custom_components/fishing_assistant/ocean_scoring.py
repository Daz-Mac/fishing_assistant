"""Ocean fishing scoring algorithm with improved astronomical calculations."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple, Any

from .base_scorer import BaseScorer
from .const import (
    CONF_SPECIES_ID,
    CONF_HABITAT_PRESET,
    HABITAT_PRESETS,
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
from .helpers.astro import calculate_astronomy_forecast

_LOGGER = logging.getLogger(__name__)


class OceanFishingScorer(BaseScorer):
    """Calculate ocean fishing scores based on conditions and species."""

    def __init__(
        self,
        hass,
        config: Dict,
        species_profiles: dict[str, Any] = None
    ):
        """Initialize the scorer."""
        self.hass = hass
        self.config = config
        self.species_loader = SpeciesLoader(hass)
        self.species_profile = None
        self._initialized = False
        self._astro_forecast_cache = None
        self._astro_cache_time = None
        
        # Initialize BaseScorer with species profiles
        if species_profiles is None:
            species_profiles = {}
        
        # Get coordinates from config
        latitude = config.get("latitude", 0.0)
        longitude = config.get("longitude", 0.0)
        species_id = config.get(CONF_SPECIES_ID, "general_mixed")
        
        super().__init__(
            latitude=latitude,
            longitude=longitude,
            species=[species_id],
            species_profiles=species_profiles
        )

    async def async_initialize(self):
        """Initialize the scorer asynchronously."""
        if self._initialized:
            return

        try:
            await self.species_loader.async_load_profiles()

            # Load species profile
            species_id = self.config.get(CONF_SPECIES_ID, "general_mixed")
            self.species_profile = self.species_loader.get_species(species_id)

            if not self.species_profile:
                _LOGGER.warning(
                    "Species profile '%s' not found, using fallback", species_id
                )
                self.species_profile = self._get_fallback_profile()
            else:
                _LOGGER.info(
                    "Loaded species profile: %s",
                    self.species_profile.get("name", species_id)
                )
                # Update species_profiles dict for BaseScorer
                self.species_profiles[species_id] = self.species_profile

            # Pre-load astronomical forecast
            await self._refresh_astro_cache()

            self._initialized = True

        except Exception as e:
            _LOGGER.error("Error initializing ocean scorer: %s", e, exc_info=True)
            self.species_profile = self._get_fallback_profile()
            self._initialized = True

    async def _refresh_astro_cache(self):
        """Refresh astronomical forecast cache."""
        try:
            latitude = self.config.get("latitude")
            longitude = self.config.get("longitude")
            
            if latitude is None or longitude is None:
                _LOGGER.warning("No coordinates configured, using fallback astro data")
                return
            
            _LOGGER.info("Refreshing astronomical forecast cache")
            self._astro_forecast_cache = await calculate_astronomy_forecast(
                self.hass,
                latitude,
                longitude,
                days=7
            )
            self._astro_cache_time = datetime.now()
            _LOGGER.debug("Astronomical cache refreshed with %d days", len(self._astro_forecast_cache))
        except Exception as e:
            _LOGGER.error("Error refreshing astro cache: %s", e, exc_info=True)
            self._astro_forecast_cache = None

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

    # Implement abstract methods from BaseScorer
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
            tide_data: Optional formatted tide data
            marine_data: Optional formatted marine data
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
        
        # Tide Score
        if tide_data:
            tide_state = tide_data.get("state", "unknown")
            tide_strength = tide_data.get("strength", 50) / 100.0
            components["tide"] = self._score_tide(tide_state, tide_strength)
        else:
            components["tide"] = 5.0
        
        # Wave Score
        if marine_data:
            wave_height = marine_data.get("current", {}).get("wave_height", 1.0)
            components["waves"] = self._score_waves(wave_height)
        else:
            components["waves"] = 5.0
        
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
            "tide": 0.25,
            "wind": 0.15,
            "waves": 0.15,
            "time_of_day": 0.15,
            "pressure": 0.10,
            "season": 0.10,
            "moon": 0.05,
            "temperature": 0.03,
            "cloud_cover": 0.02,
        }

    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature."""
        # Ocean fishing is less temperature-sensitive than freshwater
        if 10 <= temperature <= 25:
            return 10.0
        elif 5 <= temperature <= 30:
            return 7.0
        else:
            return 5.0

    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        """Score based on wind conditions."""
        if wind_speed < 5:
            return 6.0  # Too calm
        elif wind_speed < 15:
            return 10.0  # Ideal
        elif wind_speed < 25:
            return 7.0  # Moderate
        elif wind_speed < 35:
            return 4.0  # Strong
        else:
            return 2.0  # Dangerous

    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure."""
        if 1013 <= pressure <= 1020:
            return 10.0
        elif 1008 <= pressure < 1013:
            return 8.0  # Slightly low, often good
        elif 1020 < pressure <= 1025:
            return 7.0  # Slightly high
        elif 1000 <= pressure < 1008:
            return 6.0  # Low pressure
        elif pressure > 1025:
            return 5.0  # High pressure
        else:
            return 4.0  # Very low pressure

    def _score_tide(self, tide_state: str, tide_strength: float) -> float:
        """Score based on tide conditions."""
        if not self.species_profile:
            return 5.0
        
        best_tide = self.species_profile.get("best_tide", "moving")
        
        try:
            tide_strength = max(0.0, min(1.0, float(tide_strength)))
        except (ValueError, TypeError):
            tide_strength = 0.5

        if best_tide == "any":
            return 8.0
        elif best_tide == "moving":
            if tide_state in [TIDE_STATE_RISING, TIDE_STATE_FALLING]:
                return 7.0 + (tide_strength * 3.0)
            else:
                return 5.0
        elif best_tide == "rising":
            if tide_state == TIDE_STATE_RISING:
                return 8.0 + (tide_strength * 2.0)
            else:
                return 5.0
        elif best_tide == "falling":
            if tide_state == TIDE_STATE_FALLING:
                return 8.0 + (tide_strength * 2.0)
            else:
                return 5.0
        elif best_tide == "slack":
            if tide_state in [TIDE_STATE_SLACK_HIGH, TIDE_STATE_SLACK_LOW]:
                return 9.0
            else:
                return 5.0
        elif best_tide == "slack_high":
            if tide_state == TIDE_STATE_SLACK_HIGH:
                return 10.0
            else:
                return 5.0
        elif best_tide == "slack_low":
            if tide_state == TIDE_STATE_SLACK_LOW:
                return 10.0
            else:
                return 5.0
        
        return 5.0

    def _score_waves(self, wave_height: float) -> float:
        """Score based on wave conditions."""
        try:
            wave_height = max(0.0, float(wave_height))
        except (ValueError, TypeError):
            return 5.0

        if not self.species_profile:
            return 5.0

        wave_pref = self.species_profile.get("wave_preference", "moderate")
        wave_bonus = self.species_profile.get("wave_bonus", False)

        if wave_pref == "calm":
            if wave_height < 0.5:
                score = 10.0
            elif wave_height < 1.0:
                score = 7.0
            elif wave_height < 1.5:
                score = 4.0
            else:
                score = 2.0
        elif wave_pref == "moderate":
            if wave_height < 0.5:
                score = 6.0
            elif wave_height < 1.5:
                score = 10.0
            elif wave_height < 2.5:
                score = 7.0
            else:
                score = 3.0
        elif wave_pref == "active":
            if wave_height < 1.0:
                score = 5.0
            elif wave_height < 2.5:
                score = 10.0
            elif wave_height < 3.5:
                score = 8.0
            else:
                score = 3.0
        else:  # any
            score = 7.0

        # Apply wave bonus if species benefits from waves
        if wave_bonus and wave_height > 1.0:
            score = min(10.0, score + 2.0)

        return score

    def _score_cloud_cover(self, cloud_cover: float) -> float:
        """Score based on cloud cover."""
        cloud_bonus = self.species_profile.get("cloud_bonus", 0.5) if self.species_profile else 0.5
        try:
            cloud_bonus = max(0.0, min(1.0, float(cloud_bonus)))
            cloud_cover = max(0.0, min(100.0, float(cloud_cover)))
        except (ValueError, TypeError):
            return 5.0
        
        # Base score + cloud preference
        return 5.0 + (cloud_cover / 100 * cloud_bonus * 5.0)

    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase."""
        if moon_phase is None:
            return 5.0
        
        try:
            moon_phase = max(0.0, min(1.0, float(moon_phase)))
        except (ValueError, TypeError):
            return 5.0

        # New moon (0) and full moon (0.5) are typically best
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 10.0  # New moon
        elif 0.4 < moon_phase < 0.6:
            return 9.0  # Full moon
        elif 0.2 < moon_phase < 0.3 or 0.7 < moon_phase < 0.8:
            return 6.0  # Quarter moons
        else:
            return 7.0  # In between

    def _score_time_of_day(self, current_time: datetime, astro: Dict[str, Any]) -> float:
        """Score based on time of day."""
        light_condition = self._determine_light_condition(astro, current_time)
        
        if not self.species_profile:
            return 5.0
        
        light_pref = self.species_profile.get("light_preference", "dawn_dusk")
        
        score_map = {
            "day": {LIGHT_DAY: 10.0, LIGHT_DAWN: 7.0, LIGHT_DUSK: 7.0, LIGHT_NIGHT: 3.0},
            "night": {LIGHT_NIGHT: 10.0, LIGHT_DUSK: 7.0, LIGHT_DAWN: 6.0, LIGHT_DAY: 2.0},
            "dawn": {LIGHT_DAWN: 10.0, LIGHT_DAY: 7.0, LIGHT_DUSK: 6.0, LIGHT_NIGHT: 4.0},
            "dusk": {LIGHT_DUSK: 10.0, LIGHT_NIGHT: 7.0, LIGHT_DAWN: 6.0, LIGHT_DAY: 4.0},
            "dawn_dusk": {LIGHT_DAWN: 10.0, LIGHT_DUSK: 10.0, LIGHT_DAY: 6.0, LIGHT_NIGHT: 5.0},
            "low_light": {LIGHT_DAWN: 10.0, LIGHT_DUSK: 10.0, LIGHT_NIGHT: 9.0, LIGHT_DAY: 4.0},
        }
        
        return score_map.get(light_pref, {}).get(light_condition, 5.0)

    def _score_season(self, current_time: datetime) -> float:
        """Score based on season/active months."""
        if not self.species_profile or not current_time:
            return 5.0

        try:
            current_month = current_time.month
        except AttributeError:
            return 5.0

        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        
        if not active_months:
            return 7.0

        if current_month in active_months:
            return 10.0
        else:
            # Check if we're close to active season
            try:
                months_to_season = min(
                    abs(current_month - m) if abs(current_month - m) <= 6
                    else 12 - abs(current_month - m)
                    for m in active_months
                )
                
                if months_to_season == 1:
                    return 6.0
                elif months_to_season == 2:
                    return 4.0
                else:
                    return 2.0
            except (ValueError, TypeError):
                return 2.0

    def _determine_light_condition(
        self, astro_data: Dict, current_time: datetime = None
    ) -> str:
        """Determine light condition for a specific time."""
        if current_time is None:
            current_time = datetime.now()

        if not astro_data:
            return self._fallback_light_condition(current_time)

        sunrise = astro_data.get("sunrise")
        sunset = astro_data.get("sunset")

        if not sunrise or not sunset:
            return self._fallback_light_condition(current_time)

        try:
            # Normalize all datetimes to be timezone-naive for comparison
            if current_time.tzinfo is not None:
                current_time = current_time.replace(tzinfo=None)
            if sunrise.tzinfo is not None:
                sunrise = sunrise.replace(tzinfo=None)
            if sunset.tzinfo is not None:
                sunset = sunset.replace(tzinfo=None)

            # Calculate dawn and dusk periods (30 min before/after)
            dawn_start = sunrise - timedelta(minutes=30)
            dawn_end = sunrise + timedelta(minutes=30)
            dusk_start = sunset - timedelta(minutes=30)
            dusk_end = sunset + timedelta(minutes=30)

            if dawn_start <= current_time <= dawn_end:
                return LIGHT_DAWN
            elif dusk_start <= current_time <= dusk_end:
                return LIGHT_DUSK
            elif sunrise < current_time < sunset:
                return LIGHT_DAY
            else:
                return LIGHT_NIGHT
        except Exception as e:
            _LOGGER.debug("Error determining light condition: %s", e)
            return self._fallback_light_condition(current_time)
    
    def _fallback_light_condition(self, current_time: datetime) -> str:
        """Fallback light condition based on hour."""
        hour = current_time.hour
        if 6 <= hour < 8:
            return LIGHT_DAWN
        elif 8 <= hour < 18:
            return LIGHT_DAY
        elif 18 <= hour < 20:
            return LIGHT_DUSK
        else:
            return LIGHT_NIGHT

    def check_safety(
        self, weather_data: Dict, marine_data: Dict
    ) -> Tuple[str, List[str]]:
        """Check if conditions are safe for fishing.

        Returns:
            tuple: (safety_status, list of reasons)
        """
        if not weather_data and not marine_data:
            return "unknown", ["Insufficient data to assess safety"]
        
        habitat_preset = self.config.get(CONF_HABITAT_PRESET, "rocky_point")
        habitat = HABITAT_PRESETS.get(habitat_preset, HABITAT_PRESETS.get("rocky_point", {}))
        
        if not habitat:
            _LOGGER.warning("No habitat preset found, using defaults")
            habitat = {"max_wind_speed": 30, "max_gust_speed": 45, "max_wave_height": 2.5}

        wind_speed = weather_data.get("wind_speed", 0) if weather_data else 0
        wind_gust = weather_data.get("wind_gust", wind_speed) if weather_data else wind_speed
        wave_height = marine_data.get("current", {}).get("wave_height", 0) if marine_data else 0
        precipitation = weather_data.get("precipitation_probability", 0) if weather_data else 0

        max_wind = habitat.get("max_wind_speed", 30)
        max_gust = habitat.get("max_gust_speed", 45)
        max_wave = habitat.get("max_wave_height", 2.5)

        reasons = []
        unsafe_count = 0
        caution_count = 0

        # Check wind speed
        try:
            wind_speed_val = float(wind_speed)
            if wind_speed_val > max_wind:
                reasons.append(f"High wind: {round(wind_speed_val)} km/h (max: {max_wind})")
                unsafe_count += 1
            elif wind_speed_val > max_wind * 0.8:
                reasons.append(
                    f"Strong wind: {round(wind_speed_val)} km/h "
                    f"(caution at {round(max_wind * 0.8)})"
                )
                caution_count += 1
        except (ValueError, TypeError):
            pass

        # Check wind gusts
        try:
            wind_gust_val = float(wind_gust)
            if wind_gust_val > max_gust:
                reasons.append(f"Dangerous gusts: {round(wind_gust_val)} km/h (max: {max_gust})")
                unsafe_count += 1
            elif wind_gust_val > max_gust * 0.8:
                reasons.append(
                    f"Strong gusts: {round(wind_gust_val)} km/h "
                    f"(caution at {round(max_gust * 0.8)})"
                )
                caution_count += 1
        except (ValueError, TypeError):
            pass

        # Check wave height
        try:
            wave_height_val = float(wave_height)
            if wave_height_val > max_wave:
                reasons.append(f"High waves: {round(wave_height_val, 1)}m (max: {max_wave}m)")
                unsafe_count += 1
            elif wave_height_val > max_wave * 0.8:
                reasons.append(
                    f"Large waves: {round(wave_height_val, 1)}m "
                    f"(caution at {round(max_wave * 0.8, 1)}m)"
                )
                caution_count += 1
        except (ValueError, TypeError):
            pass

        # Check precipitation
        try:
            precip_val = float(precipitation)
            if precip_val > 70:
                reasons.append(f"Heavy rain likely: {int(precip_val)}%")
                caution_count += 1
            elif precip_val > 50:
                reasons.append(f"Rain likely: {int(precip_val)}%")
                caution_count += 1
        except (ValueError, TypeError):
            pass

        # Determine overall safety status
        if unsafe_count > 0:
            return "unsafe", reasons
        elif caution_count > 0:
            return "caution", reasons
        else:
            return "safe", ["Conditions within safe limits"]