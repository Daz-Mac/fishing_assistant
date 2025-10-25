"""Freshwater fishing scoring algorithm with period-based forecasting."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
from zoneinfo import ZoneInfo

from .base_scorer import BaseScorer
from .data_schema import (
    WeatherConditions,
    FishingScore,
    HourlyForecast,
    SpeciesScore
)
from .data_formatter import DataFormatter
from .const import (
    CONF_FISH,
    CONF_BODY_TYPE,
    CONF_TIME_PERIODS,
    TIME_PERIODS_FULL_DAY,
    TIME_PERIODS_DAWN_DUSK,
    TIME_PERIOD_DEFINITIONS,
    WEATHER_CONDITION_SCORES,
    WIND_SPEED_THRESHOLDS,
    PRESSURE_THRESHOLDS,
    CLOUD_COVER_THRESHOLDS,
    MOON_PHASE_SCORES,
    SOLUNAR_PERIOD_BONUS
)
from .species_loader import SpeciesLoader
from .helpers.astro import calculate_astronomy_forecast

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
        
        self.formatter = DataFormatter()

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        target_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Calculate base fishing score.
        
        Returns:
            Dictionary with score and component breakdown
        """
        if target_time is None:
            target_time = datetime.now()
        
        components = {}
        total_adjustment = 0.0
        
        # Season/Activity Score
        current_month = target_time.month
        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        if current_month in active_months:
            season_score = 1.0
            season_multiplier = 1.0
        else:
            season_score = 0.3
            season_multiplier = 0.3
        components["Season"] = season_score

        # Temperature Score
        temp = weather_data.get("temperature")
        temp_score = 0.5
        temp_adjustment = 0.0
        if temp is not None:
            temp_range = self.species_profile.get("temp_range", [5, 30])
            if len(temp_range) == 2:
                min_temp, max_temp = temp_range
                temp_span = max_temp - min_temp
                optimal_min = min_temp + (temp_span * 0.2)
                optimal_max = max_temp - (temp_span * 0.2)

                if optimal_min <= temp <= optimal_max:
                    temp_score = 1.0
                    temp_adjustment = 0.3
                elif min_temp <= temp <= max_temp:
                    temp_score = 0.7
                    temp_adjustment = 0.1
                else:
                    if temp < min_temp:
                        distance = min_temp - temp
                    else:
                        distance = temp - max_temp
                    temp_score = max(0.2, 0.7 - (distance * 0.05))
                    temp_adjustment = -0.2
        components["Temperature"] = temp_score
        total_adjustment += temp_adjustment

        # Cloud Cover Score
        cloud_cover = weather_data.get("cloud_cover", 50)
        ideal_cloud = self.species_profile.get("ideal_cloud", 50)
        cloud_diff = abs(cloud_cover - ideal_cloud)
        
        if cloud_diff <= 15:
            cloud_score = 1.0
            cloud_adjustment = 0.15
        elif cloud_diff <= 30:
            cloud_score = 0.7
            cloud_adjustment = 0.0
        else:
            cloud_score = 0.4
            cloud_adjustment = -0.1
        components["Cloud Cover"] = cloud_score
        total_adjustment += cloud_adjustment

        # Wind Score
        wind_speed = weather_data.get("wind_speed", 0)
        if 5 <= wind_speed <= 15:
            wind_score = 1.0
            wind_adjustment = 0.1
        elif wind_speed > 25:
            wind_score = 0.3
            wind_adjustment = -0.2
        else:
            wind_score = 0.7
            wind_adjustment = 0.0
        components["Wind"] = wind_score
        total_adjustment += wind_adjustment

        # Pressure Score
        pressure = weather_data.get("pressure", 1013)
        prefers_low = self.species_profile.get("prefers_low_pressure", False)
        
        if prefers_low:
            if pressure < 1010:
                pressure_score = 1.0
                pressure_adjustment = 0.15
            elif pressure < 1015:
                pressure_score = 0.8
                pressure_adjustment = 0.05
            else:
                pressure_score = 0.5
                pressure_adjustment = -0.05
        else:
            if 1013 <= pressure <= 1020:
                pressure_score = 1.0
                pressure_adjustment = 0.15
            elif 1010 <= pressure <= 1025:
                pressure_score = 0.7
                pressure_adjustment = 0.0
            else:
                pressure_score = 0.4
                pressure_adjustment = -0.1
        components["Pressure"] = pressure_score
        total_adjustment += pressure_adjustment

        # Time of Day Score
        hour = target_time.hour
        if 5 <= hour <= 8 or 17 <= hour <= 20:
            time_score = 1.0
            time_adjustment = 0.2
        else:
            time_score = 0.6
            time_adjustment = 0.0
        components["Time of Day"] = time_score
        total_adjustment += time_adjustment

        # Calculate final score
        base_score = 0.5
        final_score = (base_score + total_adjustment) * season_multiplier
        final_score = max(0.0, min(1.0, final_score))
        
        # Scale to 0-10
        final_score_scaled = final_score * 10

        return {
            "score": round(final_score_scaled, 1),
            "components": components
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
            "cloud_cover": 0.15,
            "time_of_day": 0.15,
            "season": 0.15,
        }

    def calculate_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        target_time: Optional[datetime] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Calculate fishing score for current conditions.
        
        Args:
            weather_data: Weather data dictionary
            astro_data: Astronomical data dictionary
            target_time: Optional target datetime
            
        Returns:
            Dictionary with score, breakdown, and component scores
        """
        if target_time is None:
            target_time = datetime.now()
        
        try:
            result = self._calculate_base_score(weather_data, astro_data, target_time)
            
            # Generate summary
            score = result["score"]
            if score >= 7:
                summary = "Excellent conditions"
            elif score >= 4:
                summary = "Good conditions"
            else:
                summary = "Poor conditions"
            
            return {
                "score": result["score"],
                "breakdown": {
                    "species": self.species_name,
                    "body_type": self.body_type,
                },
                "component_scores": result["components"],
                "conditions_summary": summary,
            }
        except Exception as e:
            _LOGGER.error(f"Error calculating freshwater score: {e}", exc_info=True)
            return {
                "score": 0.0,
                "breakdown": {},
                "component_scores": {},
                "conditions_summary": "Error calculating score",
            }

    async def calculate_forecast(
        self,
        weather_entity_id: str,
        latitude: float,
        longitude: float,
        period_type: str = TIME_PERIODS_FULL_DAY,
        days: int = 7,
    ) -> Dict:
        """Calculate fishing score forecast.
        
        Args:
            weather_entity_id: Home Assistant weather entity ID
            latitude: Location latitude
            longitude: Location longitude
            period_type: Time period type (full_day or dawn_dusk)
            days: Number of days to forecast
            
        Returns:
            Dictionary with forecast data
        """
        # This method needs access to hass, which should be passed through
        # For now, return empty dict - this will be called from sensor.py
        # which has access to hass
        return {}

    # Implement abstract methods from BaseScorer
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

    def _score_time_of_day(self, current_time: Any, astro: Dict[str, Any]) -> float:
        """Score based on time of day."""
        hour = current_time.hour
        if 5 <= hour <= 8 or 17 <= hour <= 20:
            return 10.0
        else:
            return 6.0


# Legacy function maintained for backward compatibility
def get_fish_score(
    hass,
    fish_list: List[str],
    body_type: str,
    weather_data: Dict,
    astro_data: Dict,
    species_loader: SpeciesLoader,
    target_time: Optional[datetime] = None,
) -> Dict:
    """Calculate fishing score for current conditions."""
    if target_time is None:
        target_time = datetime.now()

    # Get species profiles
    profiles = []
    for fish_id in fish_list:
        profile = species_loader.get_species(fish_id)
        if profile:
            profiles.append(profile)
        else:
            _LOGGER.warning("Species profile not found: %s", fish_id)

    if not profiles:
        _LOGGER.error("No valid species profiles found")
        return {
            "score": 0.0,
            "breakdown": {},
            "component_scores": {},
            "conditions_summary": "No species data available",
        }

    # Calculate scores for each species
    species_scores = []
    all_component_scores = []
    for profile in profiles:
        score_data = _calculate_species_score(
            profile, body_type, weather_data, astro_data, target_time
        )
        species_scores.append(score_data["score"])
        all_component_scores.append(score_data["components"])

    # Average the scores
    final_score = sum(species_scores) / len(species_scores)

    # Average component scores across all species
    component_scores = {}
    if all_component_scores:
        # Get all component keys
        all_keys = set()
        for comp in all_component_scores:
            all_keys.update(comp.keys())
        
        # Average each component
        for key in all_keys:
            values = [comp.get(key, 0) for comp in all_component_scores]
            component_scores[key] = round(sum(values) / len(values), 2)

    # Generate breakdown
    breakdown = {
        "species_count": len(profiles),
        "body_type": body_type,
        "target_species": [p.get("name", p["id"]) for p in profiles],
    }

    # Scale to 0-10 (matching ocean scoring)
    final_score_scaled = final_score * 10

    # Generate summary (using scaled score)
    if final_score_scaled >= 7:
        summary = "Excellent conditions"
    elif final_score_scaled >= 4:
        summary = "Good conditions"
    else:
        summary = "Poor conditions"

    return {
        "score": round(final_score_scaled, 1),
        "breakdown": breakdown,
        "component_scores": component_scores,
        "conditions_summary": summary,
    }


async def get_fish_score_forecast(
    hass,
    fish_list: List[str],
    body_type: str,
    weather_entity_id: str,
    latitude: float,
    longitude: float,
    species_loader: SpeciesLoader,
    period_type: str = TIME_PERIODS_FULL_DAY,
    days: int = 7,
) -> Dict:
    """Calculate fishing score forecast for the next N days with period-based scoring."""
    forecast = {}

    try:
        # Get weather forecast
        weather_state = hass.states.get(weather_entity_id)
        if not weather_state:
            _LOGGER.warning("Weather entity not found: %s", weather_entity_id)
            return {}

        # Try to get forecast using the new service call method (HA 2023.9+)
        weather_forecast = []
        try:
            service_response = await hass.services.async_call(
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
            # Fallback to old attribute method
            weather_forecast = weather_state.attributes.get("forecast", [])

        if not weather_forecast:
            _LOGGER.warning("No weather forecast available")
            return {}

        # Get astronomical forecast
        astro_forecast = await calculate_astronomy_forecast(hass, latitude, longitude, days=days)

        # Get time period configuration
        time_period_def = TIME_PERIOD_DEFINITIONS.get(
            period_type, TIME_PERIOD_DEFINITIONS[TIME_PERIODS_FULL_DAY]
        )

        # Get current time with timezone awareness
        now = datetime.now()
        if now.tzinfo is None:
            try:
                tz_str = hass.config.time_zone
                now = now.replace(tzinfo=ZoneInfo(tz_str))
            except Exception as e:
                _LOGGER.debug("Could not get HA timezone, using naive datetime: %s", e)

        # Process each day
        for day_offset in range(days):
            target_date = now.date() + timedelta(days=day_offset)
            date_key = target_date.isoformat()
            is_today = (day_offset == 0)

            # Get weather for this day
            day_weather = _get_weather_for_date(weather_forecast, target_date)
            if not day_weather:
                continue

            # Get astro data for this day
            day_astro = astro_forecast.get(date_key, {})

            # Initialize day forecast
            forecast[date_key] = {
                "date": date_key,
                "day_name": target_date.strftime("%A"),
                "periods": {},  # Dictionary, not array!
            }

            # Get time blocks for this day
            time_blocks = await _get_time_blocks_for_date(
                hass, target_date, time_period_def, day_astro
            )

            # Calculate score for each time block
            for block in time_blocks:
                # Skip past/current periods for today
                if is_today and block["name"] != "night":
                    block_start_time = datetime.combine(
                        target_date,
                        datetime.min.time().replace(
                            hour=block["start_hour"],
                            minute=block.get("start_minute", 0)
                        )
                    )
                    
                    if now.tzinfo is not None and block_start_time.tzinfo is None:
                        try:
                            tz_str = hass.config.time_zone
                            block_start_time = block_start_time.replace(tzinfo=ZoneInfo(tz_str))
                        except Exception:
                            pass
                    
                    if now >= block_start_time:
                        _LOGGER.debug(
                            "Skipping current/past period %s for today", block["name"]
                        )
                        continue

                target_time = datetime.combine(
                    target_date,
                    datetime.min.time().replace(
                        hour=block["start_hour"],
                        minute=block.get("start_minute", 0)
                    )
                )

                # Calculate score for this period
                result = get_fish_score(
                    hass=hass,
                    fish_list=fish_list,
                    body_type=body_type,
                    weather_data=day_weather,
                    astro_data=day_astro,
                    species_loader=species_loader,
                    target_time=target_time,
                )

                # Format time range
                start_min = block.get("start_minute", 0)
                end_min = block.get("end_minute", 0)
                hours_display = f"{block['start_hour']:02d}:{start_min:02d}-{block['end_hour']:02d}:{end_min:02d}"

                # Store period data as dictionary entry (not array element!)
                forecast[date_key]["periods"][block["name"]] = {
                    "time_block": block["name"],
                    "hours": hours_display,
                    "score": result["score"],
                    "component_scores": result.get("component_scores", {}),
                    "safety": "safe",  # Freshwater doesn't have marine safety checks
                    "safety_reasons": ["Conditions within normal limits"],
                    "tide_state": "n/a",  # No tides in freshwater
                    "conditions": result["conditions_summary"],
                    "weather": {
                        "temperature": day_weather.get("temperature"),
                        "wind_speed": day_weather.get("wind_speed", 0),
                        "wind_gust": day_weather.get("wind_gust", day_weather.get("wind_speed", 0)),
                        "cloud_cover": day_weather.get("cloud_cover", 50),
                        "precipitation_probability": day_weather.get("precipitation_probability", 0),
                        "pressure": day_weather.get("pressure", 1013),
                    },
                }

            # Calculate daily average score (only if we have periods)
            if forecast[date_key]["periods"]:
                period_scores = [
                    p["score"] for p in forecast[date_key]["periods"].values()
                ]
                forecast[date_key]["daily_avg_score"] = round(
                    sum(period_scores) / len(period_scores), 2
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


def _calculate_species_score(
    profile: Dict,
    body_type: str,
    weather_data: Dict,
    astro_data: Dict,
    target_time: datetime,
) -> Dict:
    """Calculate score for a single species with component breakdown."""
    components = {}
    total_adjustment = 0.0
    
    # Season/Activity Score
    current_month = target_time.month
    active_months = profile.get("active_months", list(range(1, 13)))
    if current_month in active_months:
        season_score = 1.0
        season_multiplier = 1.0
    else:
        season_score = 0.3
        season_multiplier = 0.3
    components["Season"] = season_score

    # Temperature Score (using species temp_range)
    temp = weather_data.get("temperature")
    temp_score = 0.5
    temp_adjustment = 0.0
    if temp is not None:
        temp_range = profile.get("temp_range", [5, 30])
        if len(temp_range) == 2:
            min_temp, max_temp = temp_range
            # Calculate optimal range (middle 60% of range)
            temp_span = max_temp - min_temp
            optimal_min = min_temp + (temp_span * 0.2)
            optimal_max = max_temp - (temp_span * 0.2)

            if optimal_min <= temp <= optimal_max:
                temp_score = 1.0
                temp_adjustment = 0.3
            elif min_temp <= temp <= max_temp:
                temp_score = 0.7
                temp_adjustment = 0.1
            else:
                # Calculate how far outside range
                if temp < min_temp:
                    distance = min_temp - temp
                else:
                    distance = temp - max_temp
                temp_score = max(0.2, 0.7 - (distance * 0.05))
                temp_adjustment = -0.2
    components["Temperature"] = temp_score
    total_adjustment += temp_adjustment

    # Cloud Cover Score (using species ideal_cloud)
    cloud_cover = weather_data.get("cloud_cover", 50)
    ideal_cloud = profile.get("ideal_cloud", 50)
    cloud_diff = abs(cloud_cover - ideal_cloud)
    
    if cloud_diff <= 15:
        cloud_score = 1.0
        cloud_adjustment = 0.15
    elif cloud_diff <= 30:
        cloud_score = 0.7
        cloud_adjustment = 0.0
    else:
        cloud_score = 0.4
        cloud_adjustment = -0.1
    components["Cloud Cover"] = cloud_score
    total_adjustment += cloud_adjustment

    # Wind Score (generic for freshwater)
    wind_speed = weather_data.get("wind_speed", 0)
    if 5 <= wind_speed <= 15:
        wind_score = 1.0
        wind_adjustment = 0.1
    elif wind_speed > 25:
        wind_score = 0.3
        wind_adjustment = -0.2
    else:
        wind_score = 0.7
        wind_adjustment = 0.0
    components["Wind"] = wind_score
    total_adjustment += wind_adjustment

    # Pressure Score (using species prefers_low_pressure)
    pressure = weather_data.get("pressure", 1013)
    prefers_low = profile.get("prefers_low_pressure", False)
    
    if prefers_low:
        # Species prefers low pressure (falling barometer)
        if pressure < 1010:
            pressure_score = 1.0
            pressure_adjustment = 0.15
        elif pressure < 1015:
            pressure_score = 0.8
            pressure_adjustment = 0.05
        else:
            pressure_score = 0.5
            pressure_adjustment = -0.05
    else:
        # Species prefers stable/high pressure
        if 1013 <= pressure <= 1020:
            pressure_score = 1.0
            pressure_adjustment = 0.15
        elif 1010 <= pressure <= 1025:
            pressure_score = 0.7
            pressure_adjustment = 0.0
        else:
            pressure_score = 0.4
            pressure_adjustment = -0.1
    components["Pressure"] = pressure_score
    total_adjustment += pressure_adjustment

    # Time of Day Score (Dawn/Dusk bonus)
    hour = target_time.hour
    if 5 <= hour <= 8 or 17 <= hour <= 20:
        time_score = 1.0
        time_adjustment = 0.2
    else:
        time_score = 0.6
        time_adjustment = 0.0
    components["Time of Day"] = time_score
    total_adjustment += time_adjustment

    # Calculate final score
    base_score = 0.5
    final_score = (base_score + total_adjustment) * season_multiplier
    
    # Ensure score is between 0 and 1
    final_score = max(0.0, min(1.0, final_score))

    return {
        "score": final_score,
        "components": components
    }


def _get_weather_for_date(weather_forecast: List[Dict], target_date) -> Optional[Dict]:
    """Extract weather data for a specific date from forecast."""
    for forecast_item in weather_forecast:
        if not forecast_item:
            continue
            
        forecast_datetime = forecast_item.get("datetime")
        if not forecast_datetime:
            continue
            
        try:
            if isinstance(forecast_datetime, str):
                forecast_datetime = datetime.fromisoformat(
                    forecast_datetime.replace('Z', '+00:00')
                )
            
            if forecast_datetime.date() == target_date:
                return forecast_item
        except (ValueError, AttributeError) as e:
            _LOGGER.debug("Error parsing forecast datetime: %s", e)
            continue

    return None


async def _get_time_blocks_for_date(
    hass, target_date, time_period_def: Dict, astro_data: Dict
) -> List[Dict]:
    """Get time blocks for a specific date based on time period configuration."""
    periods = time_period_def.get("periods", [])
    time_blocks = []

    for period in periods:
        if not period:
            continue
            
        try:
            if "relative_to" in period:
                # Dawn/Dusk mode - calculate based on sunrise/sunset
                relative_to = period["relative_to"]
                offset_before = period.get("offset_before", 60)  # minutes
                offset_after = period.get("offset_after", 60)  # minutes

                reference_time = None
                if relative_to == "sunrise":
                    sunrise_str = astro_data.get("sunrise")
                    if sunrise_str:
                        try:
                            sunrise_time = datetime.strptime(sunrise_str, "%H:%M").time()
                            reference_time = datetime.combine(target_date, sunrise_time)
                        except (ValueError, TypeError):
                            pass
                elif relative_to == "sunset":
                    sunset_str = astro_data.get("sunset")
                    if sunset_str:
                        try:
                            sunset_time = datetime.strptime(sunset_str, "%H:%M").time()
                            reference_time = datetime.combine(target_date, sunset_time)
                        except (ValueError, TypeError):
                            pass

                if reference_time:
                    start_time = reference_time - timedelta(minutes=offset_before)
                    end_time = reference_time + timedelta(minutes=offset_after)

                    time_blocks.append({
                        "name": period["name"],
                        "start_hour": start_time.hour,
                        "start_minute": start_time.minute,
                        "end_hour": end_time.hour,
                        "end_minute": end_time.minute,
                        "is_relative": True,
                    })
            else:
                # Full day mode - use fixed hours
                time_blocks.append({
                    "name": period["name"],
                    "start_hour": period["start_hour"],
                    "start_minute": 0,
                    "end_hour": period["end_hour"],
                    "end_minute": 0,
                    "is_relative": False,
                })
        except Exception as e:
            _LOGGER.debug("Error processing time block: %s", e)
            continue

    return time_blocks