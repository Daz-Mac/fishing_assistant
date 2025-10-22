"""Ocean fishing scoring algorithm."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple

from .const import (
    CONF_SPECIES_ID,
    CONF_SPECIES_REGION,
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

_LOGGER = logging.getLogger(__name__)


class OceanFishingScorer:
    """Calculate ocean fishing scores based on conditions and species."""

    def __init__(self, hass, config: Dict):
        """Initialize the scorer."""
        self.hass = hass
        self.config = config
        self.species_loader = SpeciesLoader(hass)
        self.species_profile = None
        self._initialized = False

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
            
            self._initialized = True
        except Exception as e:
            _LOGGER.error("Error initializing ocean scorer: %s", e, exc_info=True)
            self.species_profile = self._get_fallback_profile()
            self._initialized = True

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
        weather_data: Dict,
        tide_data: Dict,
        marine_data: Dict,
        astro_data: Dict,
        target_time: Optional[datetime] = None,
    ) -> Dict:
        """Calculate the fishing score based on all conditions."""
        
        if not self._initialized or not self.species_profile:
            _LOGGER.error("Scorer not initialized, using fallback profile")
            self.species_profile = self._get_fallback_profile()
        
        # Use target_time if provided, otherwise use current time
        current_time = target_time if target_time else datetime.now()
        
        # Extract values from data dictionaries
        tide_state = tide_data.get("state", "unknown")
        tide_strength = tide_data.get("strength", 50) / 100.0  # Convert to 0-1
        
        current_marine = marine_data.get("current", {})
        wave_height = current_marine.get("wave_height", 1.0)
        
        wind_speed = weather_data.get("wind_speed", 0)
        cloud_cover = weather_data.get("cloud_cover", 50)
        pressure = weather_data.get("pressure", 1013)
        
        # Determine light condition
        light_condition = self._determine_light_condition(astro_data, current_time)
        
        # Get moon phase
        moon_phase = astro_data.get("moon_phase", 0.5)
        
        # Calculate component scores
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
        
        # Check safety
        safety_status, safety_reasons = self._check_safety(weather_data, marine_data)
        
        # Apply hard cap based on safety status
        if safety_status == "unsafe":
            final_score = min(final_score, 3.0)  # Cap at 30/100 (Poor)
        elif safety_status == "caution":
            final_score = min(final_score, 6.0)  # Cap at 60/100 (Good)

        return {
            "score": final_score,
            "safety": safety_status,
            "safety_reasons": safety_reasons,
            "tide_state": tide_state,
            "best_window": self._determine_best_window(astro_data, tide_data),
            "conditions_summary": self._generate_summary(scores, final_score),
            "breakdown": {
                "component_scores": scores,
                "weights": weights,
                "species": self.species_profile.get("name", "Unknown"),
            },
        }

    async def calculate_forecast(
        self,
        weather_entity_id: str,
        marine_data: Dict,
        days: int = 5,
    ) -> Dict:
        """Calculate fishing score forecast for the next N days.
        
        Uses a hybrid approach:
        - For today: Only shows remaining periods (skips past/current periods)
        - For future days: Shows all 4 periods (morning, afternoon, evening, night)
        """
        forecast = {}
        
        try:
            # Get weather forecast using the new service call method
            weather_state = self.hass.states.get(weather_entity_id)
            if not weather_state:
                _LOGGER.warning("Weather entity not found: %s", weather_entity_id)
                return {}
            
            # Try to get forecast using the new service call method (HA 2023.9+)
            weather_forecast = []
            try:
                service_response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": weather_entity_id, "type": "daily"},
                    blocking=True,
                    return_response=True,
                )
                
                if service_response and weather_entity_id in service_response:
                    weather_forecast = service_response[weather_entity_id].get("forecast", [])
                    _LOGGER.debug("Got forecast from service call: %d days", len(weather_forecast))
            except Exception as e:
                _LOGGER.debug("Service call failed, trying attribute method: %s", e)
                # Fallback to old attribute method for older HA versions
                weather_forecast = weather_state.attributes.get("forecast", [])
            
            if not weather_forecast:
                _LOGGER.warning("No weather forecast available from %s", weather_entity_id)
                return {}
            
            # Get marine forecast
            marine_forecast = marine_data.get("forecast", {})
            
            # Time blocks for each day
            time_blocks = [
                {"name": "morning", "start_hour": 6, "end_hour": 12},
                {"name": "afternoon", "start_hour": 12, "end_hour": 18},
                {"name": "evening", "start_hour": 18, "end_hour": 24},
                {"name": "night", "start_hour": 0, "end_hour": 6},
            ]
            
            # Get current time for filtering past periods
            now = datetime.now()
            
            # Process each day
            for day_offset in range(days):
                target_date = datetime.now().date() + timedelta(days=day_offset)
                date_key = target_date.isoformat()
                is_today = (day_offset == 0)
                
                forecast[date_key] = {
                    "date": date_key,
                    "day_name": target_date.strftime("%A"),
                    "periods": {},
                }
                
                # Get weather forecast for this day
                day_weather = self._get_weather_for_date(weather_forecast, target_date)
                
                # Get marine forecast for this day
                day_marine = marine_forecast.get(date_key, {})
                
                # Calculate score for each time block
                for block in time_blocks:
                    # Skip past/current periods for today (hybrid approach)
                    if is_today:
                        # For periods that don't cross midnight (morning, afternoon, evening)
                        if block["start_hour"] < block["end_hour"]:
                            block_start_time = datetime.combine(
                                target_date,
                                datetime.min.time().replace(hour=block["start_hour"])
                            )
                            
                            # Skip if this period has already started (we're in it or past it)
                            if now >= block_start_time:
                                _LOGGER.debug(
                                    "Skipping current/past period %s (starts at %s, current time is %s)",
                                    block["name"], block_start_time.strftime("%H:%M"), now.strftime("%H:%M")
                                )
                                continue
                        # Night period (0-6) is always shown for today as it's tonight
                    
                    target_time = datetime.combine(
                        target_date,
                        datetime.min.time().replace(hour=block["start_hour"])
                    )
                    
                    # Get tide data for this time
                    tide_data = await self._get_tide_for_time(target_time)
                    
                    # Get astro data for this time
                    astro_data = await self._get_astro_for_time(target_time)
                    
                    # Prepare weather data for this block
                    weather_data = {
                        "temperature": day_weather.get("temperature"),
                        "wind_speed": day_weather.get("wind_speed", 0),
                        "wind_gust": day_weather.get("wind_gust", day_weather.get("wind_speed", 0)),
                        "cloud_cover": day_weather.get("cloud_coverage", 50),
                        "precipitation_probability": day_weather.get("precipitation_probability", 0),
                        "pressure": day_weather.get("pressure", 1013),
                    }
                    
                    # Prepare marine data for this block
                    marine_block_data = {
                        "current": {
                            "wave_height": day_marine.get("wave_height_avg", 1.0),
                            "wave_period": day_marine.get("wave_period_avg"),
                        }
                    }
                    
                    # Calculate score for this time block
                    result = self.calculate_score(
                        weather_data=weather_data,
                        tide_data=tide_data,
                        marine_data=marine_block_data,
                        astro_data=astro_data,
                        target_time=target_time,
                    )
                    
                    forecast[date_key]["periods"][block["name"]] = {
                        "time_block": block["name"],
                        "hours": f"{block['start_hour']:02d}:00-{block['end_hour']:02d}:00",
                        "score": result["score"],
                        "safety": result["safety"],
                        "safety_reasons": result.get("safety_reasons", []),
                        "tide_state": result["tide_state"],
                        "conditions": result["conditions_summary"],
                    }
                
                # Calculate daily average score (only if we have periods)
                if forecast[date_key]["periods"]:
                    period_scores = [
                        p["score"] for p in forecast[date_key]["periods"].values()
                    ]
                    forecast[date_key]["daily_avg_score"] = round(
                        sum(period_scores) / len(period_scores), 1
                    )
                    
                    # Find best period of the day
                    best_period = max(
                        forecast[date_key]["periods"].items(),
                        key=lambda x: x[1]["score"]
                    )
                    forecast[date_key]["best_period"] = best_period[0]
                    forecast[date_key]["best_score"] = best_period[1]["score"]
                else:
                    # No periods available (all past for today)
                    forecast[date_key]["daily_avg_score"] = 0
                    forecast[date_key]["best_period"] = None
                    forecast[date_key]["best_score"] = 0
        
        except Exception as e:
            _LOGGER.error("Error calculating forecast: %s", e, exc_info=True)
        
        return forecast

    def _get_weather_for_date(self, weather_forecast: List[Dict], target_date) -> Dict:
        """Extract weather data for a specific date from forecast."""
        for forecast_item in weather_forecast:
            forecast_datetime = forecast_item.get("datetime")
            if isinstance(forecast_datetime, str):
                forecast_datetime = datetime.fromisoformat(forecast_datetime.replace('Z', '+00:00'))
            
            if forecast_datetime.date() == target_date:
                return forecast_item
        
        # Return default if not found
        return {
            "temperature": 15,
            "wind_speed": 10,
            "cloud_coverage": 50,
            "pressure": 1013,
        }

    async def _get_tide_for_time(self, target_time: datetime) -> Dict:
        """Get tide data for a specific time."""
        # This is a simplified version - in production, you'd calculate
        # tide state for the specific target_time
        # For now, we'll use a basic approximation
        
        hour = target_time.hour
        
        # Simple tidal approximation (2 high tides per ~25 hours)
        tide_hour = hour % 12.42
        
        if tide_hour < 3:
            state = TIDE_STATE_RISING
        elif tide_hour < 6:
            state = TIDE_STATE_SLACK_HIGH
        elif tide_hour < 9:
            state = TIDE_STATE_FALLING
        else:
            state = TIDE_STATE_SLACK_LOW
        
        return {
            "state": state,
            "strength": 70,  # Default strength
        }

    async def _get_astro_for_time(self, target_time: datetime) -> Dict:
        """Get astronomical data for a specific time."""
        # Get sun data
        sun_entity = self.hass.states.get("sun.sun")
        astro = {}
        
        if sun_entity:
            # For forecast, we'd need to calculate sunrise/sunset for target date
            # For now, use approximate times
            sunrise = target_time.replace(hour=6, minute=30, second=0)
            sunset = target_time.replace(hour=18, minute=30, second=0)
            astro["sunrise"] = sunrise
            astro["sunset"] = sunset
        
        # Get moon phase
        moon_entity = self.hass.states.get("sensor.moon")
        if moon_entity:
            phase_name = moon_entity.state
            phase_map = {
                "new_moon": 0.0,
                "waxing_crescent": 0.125,
                "first_quarter": 0.25,
                "waxing_gibbous": 0.375,
                "full_moon": 0.5,
                "waning_gibbous": 0.625,
                "last_quarter": 0.75,
                "waning_crescent": 0.875,
            }
            astro["moon_phase"] = phase_map.get(phase_name, 0.5)
        else:
            astro["moon_phase"] = 0.5
        
        return astro

    def _determine_light_condition(self, astro_data: Dict, current_time: datetime = None) -> str:
        """Determine light condition for a specific time."""
        if current_time is None:
            current_time = datetime.now()
        
        sunrise = astro_data.get("sunrise")
        sunset = astro_data.get("sunset")
        
        if not sunrise or not sunset:
            # Fallback based on hour
            hour = current_time.hour
            if 6 <= hour < 8:
                return LIGHT_DAWN
            elif 8 <= hour < 18:
                return LIGHT_DAY
            elif 18 <= hour < 20:
                return LIGHT_DUSK
            else:
                return LIGHT_NIGHT
        
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

    def _check_safety(self, weather_data: Dict, marine_data: Dict) -> Tuple[str, List[str]]:
        """Check if conditions are safe for fishing.
        
        Returns:
            tuple: (safety_status, list of reasons)
        """
        habitat_preset = self.config.get(CONF_HABITAT_PRESET, "rocky_point")
        habitat = HABITAT_PRESETS.get(habitat_preset, HABITAT_PRESETS["rocky_point"])
        
        wind_speed = weather_data.get("wind_speed", 0)
        wind_gust = weather_data.get("wind_gust", wind_speed)
        wave_height = marine_data.get("current", {}).get("wave_height", 0)
        precipitation = weather_data.get("precipitation_probability", 0)
        
        max_wind = habitat.get("max_wind_speed", 30)
        max_gust = habitat.get("max_gust_speed", 45)
        max_wave = habitat.get("max_wave_height", 2.5)
        
        reasons = []
        unsafe_count = 0
        caution_count = 0
        
        # Check wind speed
        if wind_speed > max_wind:
            reasons.append(f"High wind: {round(wind_speed)} km/h (max: {max_wind})")
            unsafe_count += 1
        elif wind_speed > max_wind * 0.8:
            reasons.append(f"Strong wind: {round(wind_speed)} km/h (caution at {round(max_wind * 0.8)})")
            caution_count += 1
        
        # Check wind gusts
        if wind_gust > max_gust:
            reasons.append(f"Dangerous gusts: {round(wind_gust)} km/h (max: {max_gust})")
            unsafe_count += 1
        elif wind_gust > max_gust * 0.8:
            reasons.append(f"Strong gusts: {round(wind_gust)} km/h (caution at {round(max_gust * 0.8)})")
            caution_count += 1
        
        # Check wave height
        if wave_height > max_wave:
            reasons.append(f"High waves: {round(wave_height, 1)}m (max: {max_wave}m)")
            unsafe_count += 1
        elif wave_height > max_wave * 0.8:
            reasons.append(f"Large waves: {round(wave_height, 1)}m (caution at {round(max_wave * 0.8, 1)}m)")
            caution_count += 1
        
        # Check precipitation
        if precipitation > 70:
            reasons.append(f"Heavy rain likely: {precipitation}%")
            caution_count += 1
        elif precipitation > 50:
            reasons.append(f"Rain likely: {precipitation}%")
            caution_count += 1
        
        # Determine overall safety status
        if unsafe_count > 0:
            return "unsafe", reasons
        elif caution_count > 0:
            return "caution", reasons
        else:
            return "safe", ["Conditions within safe limits"]

    def _determine_best_window(self, astro_data: Dict, tide_data: Dict) -> str:
        """Determine the best fishing window."""
        # Simple implementation - can be enhanced
        tide_state = tide_data.get("state", "unknown")
        
        if tide_state in [TIDE_STATE_RISING, TIDE_STATE_FALLING]:
            return "Current tide movement is favorable"
        elif tide_state == TIDE_STATE_SLACK_HIGH:
            return "Slack high tide - good for some species"
        elif tide_state == TIDE_STATE_SLACK_LOW:
            return "Slack low tide"
        else:
            return "Check tide times for best window"

    def _generate_summary(self, scores: Dict, final_score: float) -> str:
        """Generate a human-readable summary."""
        if final_score >= 8:
            quality = "Excellent"
        elif final_score >= 6:
            quality = "Good"
        elif final_score >= 4:
            quality = "Fair"
        else:
            quality = "Poor"
        
        # Find best contributing factor
        best_factor = max(scores.items(), key=lambda x: x[1])
        
        return f"{quality} conditions. Best factor: {best_factor[0]}"

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