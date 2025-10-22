"""Ocean fishing scoring algorithm with improved astronomical calculations."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from zoneinfo import ZoneInfo

from .const import (
    CONF_SPECIES_ID,
    CONF_SPECIES_REGION,
    CONF_HABITAT_PRESET,
    CONF_TIME_PERIODS,
    HABITAT_PRESETS,
    TIDE_STATE_RISING,
    TIDE_STATE_FALLING,
    TIDE_STATE_SLACK_HIGH,
    TIDE_STATE_SLACK_LOW,
    LIGHT_DAWN,
    LIGHT_DAY,
    LIGHT_DUSK,
    LIGHT_NIGHT,
    TIME_PERIODS_FULL_DAY,
    TIME_PERIODS_DAWN_DUSK,
    TIME_PERIOD_DEFINITIONS,
)
from .species_loader import SpeciesLoader
from .helpers.astro import calculate_astronomy_forecast

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
        self._astro_forecast_cache = None
        self._astro_cache_time = None

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

        # Extract values from data dictionaries with safe defaults
        tide_state = tide_data.get("state", "unknown") if tide_data else "unknown"
        tide_strength = (tide_data.get("strength", 50) / 100.0) if tide_data else 0.5  # Convert to 0-1
        
        current_marine = marine_data.get("current", {}) if marine_data else {}
        wave_height = current_marine.get("wave_height", 1.0) if current_marine else 1.0
        
        wind_speed = weather_data.get("wind_speed", 0) if weather_data else 0
        cloud_cover = weather_data.get("cloud_cover", 50) if weather_data else 50
        pressure = weather_data.get("pressure", 1013) if weather_data else 1013

        # Determine light condition
        light_condition = self._determine_light_condition(astro_data, current_time)

        # Get moon phase
        moon_phase = astro_data.get("moon_phase", 0.5) if astro_data else 0.5

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
            # Refresh astro cache if needed (once per day)
            if (self._astro_cache_time is None or 
                (datetime.now() - self._astro_cache_time).total_seconds() > 86400):
                await self._refresh_astro_cache()

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
            marine_forecast = marine_data.get("forecast", {}) if marine_data else {}

            # Get time period configuration
            time_period_mode = self.config.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY)
            time_period_def = TIME_PERIOD_DEFINITIONS.get(
                time_period_mode, 
                TIME_PERIOD_DEFINITIONS[TIME_PERIODS_FULL_DAY]
            )

            # Get current time with timezone awareness
            now = datetime.now()
            if now.tzinfo is None:
                # Try to get timezone from Home Assistant
                try:
                    tz_str = self.hass.config.time_zone
                    now = now.replace(tzinfo=ZoneInfo(tz_str))
                except Exception as e:
                    _LOGGER.debug("Could not get HA timezone, using naive datetime: %s", e)

            # Process each day
            for day_offset in range(days):
                target_date = now.date() + timedelta(days=day_offset)
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
                day_marine = marine_forecast.get(date_key, {}) if marine_forecast else {}

                # Get time blocks for this day (may need astro data for dawn/dusk)
                time_blocks = await self._get_time_blocks_for_date(target_date, time_period_def)

                # Calculate score for each time block
                for block in time_blocks:
                    # Skip past/current periods for today (hybrid approach)
                    if is_today and block["name"] != "night":
                        # For periods that don't cross midnight (morning, afternoon, evening)
                        # Night period (0-6) is always shown for today as it represents tonight
                        block_start_time = datetime.combine(
                            target_date,
                            datetime.min.time().replace(
                                hour=block["start_hour"],
                                minute=block.get("start_minute", 0)
                            )
                        )
                        
                        # Make timezone-aware if needed for comparison
                        if now.tzinfo is not None and block_start_time.tzinfo is None:
                            try:
                                tz_str = self.hass.config.time_zone
                                block_start_time = block_start_time.replace(tzinfo=ZoneInfo(tz_str))
                            except Exception:
                                pass
                        
                        # Skip if this period has already started (we're in it or past it)
                        if now >= block_start_time:
                            _LOGGER.debug(
                                "Skipping current/past period %s (starts at %s, current time is %s)",
                                block["name"], 
                                block_start_time.strftime("%H:%M"), 
                                now.strftime("%H:%M")
                            )
                            continue

                    target_time = datetime.combine(
                        target_date,
                        datetime.min.time().replace(
                            hour=block["start_hour"],
                            minute=block.get("start_minute", 0)
                        )
                    )

                    # Get tide data for this time
                    tide_data = await self._get_tide_for_time(target_time)

                    # Get astro data for this time
                    astro_data = await self._get_astro_for_time(target_time)

                    # Prepare weather data for this block with safe defaults
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

                    # Format time range
                    start_min = block.get("start_minute", 0)
                    end_min = block.get("end_minute", 0)
                    hours_display = f"{block['start_hour']:02d}:{start_min:02d}-{block['end_hour']:02d}:{end_min:02d}"

                    forecast[date_key]["periods"][block["name"]] = {
                        "time_block": block["name"],
                        "hours": hours_display,
                        "score": result["score"],
                        "safety": result["safety"],
                        "safety_reasons": result.get("safety_reasons", []),
                        "tide_state": result["tide_state"],
                        "conditions": result["conditions_summary"],
                        # Add weather data for this period
                        "weather": {
                            "temperature": weather_data.get("temperature"),
                            "wind_speed": weather_data.get("wind_speed", 0),
                            "wind_gust": weather_data.get("wind_gust", 0),
                            "cloud_cover": weather_data.get("cloud_cover", 50),
                            "precipitation_probability": weather_data.get("precipitation_probability", 0),
                            "pressure": weather_data.get("pressure", 1013),
                        },
                        # Add marine data for this period
                        "marine": {
                            "wave_height": marine_block_data["current"].get("wave_height", 1.0),
                            "wave_period": marine_block_data["current"].get("wave_period"),
                        },
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
        if not weather_forecast:
            return self._get_default_weather()
        
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

        # Return default if not found
        return self._get_default_weather()
    
    def _get_default_weather(self) -> Dict:
        """Return default weather data."""
        return {
            "temperature": 15,
            "wind_speed": 10,
            "cloud_coverage": 50,
            "pressure": 1013,
        }

    async def _get_tide_for_time(self, target_time: datetime) -> Dict:
        """Get tide data for a specific time.
        
        This is a simplified approximation. In production, you should use
        actual tide prediction data from a tide service or API.
        """
        if not target_time:
            return {"state": "unknown", "strength": 50}
        
        try:
            hour = target_time.hour
            
            # Simple tidal approximation (2 high tides per ~25 hours)
            # This is a very rough approximation and should be replaced with real data
            tide_hour = hour % 12.42
            
            if tide_hour < 3:
                state = TIDE_STATE_RISING
                strength = int(tide_hour / 3 * 100)
            elif tide_hour < 6:
                state = TIDE_STATE_SLACK_HIGH
                strength = 100 - int((tide_hour - 3) / 3 * 30)
            elif tide_hour < 9:
                state = TIDE_STATE_FALLING
                strength = 70 - int((tide_hour - 6) / 3 * 70)
            else:
                state = TIDE_STATE_SLACK_LOW
                strength = max(0, 30 - int((tide_hour - 9) / 3.42 * 30))
            
            return {
                "state": state,
                "strength": max(0, min(100, strength)),
            }
        except Exception as e:
            _LOGGER.debug("Error calculating tide for time: %s", e)
            return {"state": "unknown", "strength": 50}

    async def _get_astro_for_time(self, target_time: datetime) -> Dict:
        """Get astronomical data for a specific time using Skyfield calculations."""
        if not target_time:
            return self._get_fallback_astro()
        
        try:
            date_key = target_time.date().isoformat()
            
            # Use cached astronomical forecast if available
            if self._astro_forecast_cache and date_key in self._astro_forecast_cache:
                astro_day = self._astro_forecast_cache[date_key]
                
                # Parse sunrise/sunset times
                sunrise = None
                sunset = None
                
                if astro_day.get("sunrise"):
                    try:
                        sunrise_time = datetime.strptime(astro_day["sunrise"], "%H:%M").time()
                        sunrise = datetime.combine(target_time.date(), sunrise_time)
                    except (ValueError, TypeError) as e:
                        _LOGGER.debug("Error parsing sunrise: %s", e)
                
                if astro_day.get("sunset"):
                    try:
                        sunset_time = datetime.strptime(astro_day["sunset"], "%H:%M").time()
                        sunset = datetime.combine(target_time.date(), sunset_time)
                    except (ValueError, TypeError) as e:
                        _LOGGER.debug("Error parsing sunset: %s", e)
                
                # Get moon phase (already normalized 0-1)
                moon_phase = astro_day.get("moon_phase", 0.5)
                
                return {
                    "sunrise": sunrise,
                    "sunset": sunset,
                    "moon_phase": moon_phase,
                    "moonrise": astro_day.get("moonrise"),
                    "moonset": astro_day.get("moonset"),
                    "moon_transit": astro_day.get("moon_transit"),
                    "moon_underfoot": astro_day.get("moon_underfoot"),
                    "source": "skyfield",
                }
            else:
                _LOGGER.debug("No cached astro data for %s, using fallback", date_key)
                return self._get_fallback_astro(target_time)
                
        except Exception as e:
            _LOGGER.error("Error getting astro data: %s", e, exc_info=True)
            return self._get_fallback_astro(target_time)

    def _get_fallback_astro(self, target_time: Optional[datetime] = None) -> Dict:
        """Get fallback astronomical data from Home Assistant entities."""
        astro = {}
        
        if target_time is None:
            target_time = datetime.now()
        
        try:
            # Get sun data
            sun_entity = self.hass.states.get("sun.sun")
            
            if sun_entity:
                # For forecast, we'd need to calculate sunrise/sunset for target date
                # For now, use approximate times based on current sun entity
                sunrise_attr = sun_entity.attributes.get("next_rising")
                sunset_attr = sun_entity.attributes.get("next_setting")
                
                # Use approximate times if attributes not available
                if sunrise_attr:
                    try:
                        sunrise = datetime.fromisoformat(str(sunrise_attr))
                        # Adjust to target date
                        sunrise = target_time.replace(
                            hour=sunrise.hour, 
                            minute=sunrise.minute, 
                            second=0
                        )
                    except (ValueError, AttributeError):
                        sunrise = target_time.replace(hour=6, minute=30, second=0)
                else:
                    sunrise = target_time.replace(hour=6, minute=30, second=0)
                
                if sunset_attr:
                    try:
                        sunset = datetime.fromisoformat(str(sunset_attr))
                        # Adjust to target date
                        sunset = target_time.replace(
                            hour=sunset.hour, 
                            minute=sunset.minute, 
                            second=0
                        )
                    except (ValueError, AttributeError):
                        sunset = target_time.replace(hour=18, minute=30, second=0)
                else:
                    sunset = target_time.replace(hour=18, minute=30, second=0)
                
                astro["sunrise"] = sunrise
                astro["sunset"] = sunset
            else:
                # Fallback to approximate times
                astro["sunrise"] = target_time.replace(hour=6, minute=30, second=0)
                astro["sunset"] = target_time.replace(hour=18, minute=30, second=0)

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
            
            astro["source"] = "fallback"

        except Exception as e:
            _LOGGER.debug("Error getting fallback astro data: %s", e)
            astro["moon_phase"] = 0.5
            astro["sunrise"] = target_time.replace(hour=6, minute=30, second=0)
            astro["sunset"] = target_time.replace(hour=18, minute=30, second=0)
            astro["source"] = "fallback"

        return astro

    async def _get_time_blocks_for_date(
        self, target_date, time_period_def: Dict
    ) -> List[Dict]:
        """Get time blocks for a specific date based on time period configuration."""
        if not time_period_def:
            return []
        
        periods = time_period_def.get("periods", [])
        time_blocks = []

        for period in periods:
            if not period:
                continue
                
            try:
                if "relative_to" in period:
                    # Dawn/Dusk mode - calculate based on sunrise/sunset
                    # Get astro data for this date
                    target_time = datetime.combine(
                        target_date, 
                        datetime.min.time().replace(hour=12)
                    )
                    astro_data = await self._get_astro_for_time(target_time)

                    relative_to = period["relative_to"]
                    offset_before = period.get("offset_before", 60)  # minutes
                    offset_after = period.get("offset_after", 60)  # minutes

                    reference_time = None
                    if relative_to == "sunrise":
                        reference_time = astro_data.get("sunrise")
                    elif relative_to == "sunset":
                        reference_time = astro_data.get("sunset")

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

    def _check_safety(
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

    def _determine_best_window(self, astro_data: Dict, tide_data: Dict) -> str:
        """Determine the best fishing window."""
        if not tide_data:
            return "Tide data unavailable"
        
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
        if not scores:
            return "Insufficient data for summary"
        
        if final_score >= 8:
            quality = "Excellent"
        elif final_score >= 6:
            quality = "Good"
        elif final_score >= 4:
            quality = "Fair"
        else:
            quality = "Poor"

        # Find best contributing factor
        try:
            best_factor = max(scores.items(), key=lambda x: x[1])
            return f"{quality} conditions. Best factor: {best_factor[0]}"
        except (ValueError, KeyError):
            return f"{quality} conditions"

    def _score_tide(self, tide_state: str, tide_strength: float) -> float:
        """Score based on tide conditions (0-1)."""
        if not self.species_profile:
            return 0.5
        
        best_tide = self.species_profile.get("best_tide", "moving")
        score = 0.5  # Base score

        try:
            tide_strength = max(0.0, min(1.0, float(tide_strength)))
        except (ValueError, TypeError):
            tide_strength = 0.5

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
        try:
            wind_speed = max(0.0, float(wind_speed))
            cloud_cover = max(0.0, min(100.0, float(cloud_cover)))
        except (ValueError, TypeError):
            return 0.5

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
        cloud_bonus = self.species_profile.get("cloud_bonus", 0.5) if self.species_profile else 0.5
        try:
            cloud_bonus = max(0.0, min(1.0, float(cloud_bonus)))
        except (ValueError, TypeError):
            cloud_bonus = 0.5
        
        cloud_score = 0.5 + (cloud_cover / 100 * cloud_bonus)

        return (wind_score * 0.6) + (cloud_score * 0.4)

    def _score_waves(self, wave_height: float) -> float:
        """Score based on wave conditions (0-1)."""
        try:
            wave_height = max(0.0, float(wave_height))
        except (ValueError, TypeError):
            return 0.5

        if not self.species_profile:
            return 0.5

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
        if not self.species_profile:
            return 0.5

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
        try:
            moon_phase = max(0.0, min(1.0, float(moon_phase)))
        except (ValueError, TypeError):
            return 0.5

        # New moon (0) and full moon (0.5) are typically best
        # Quarter moons (0.25, 0.75) are less ideal
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 1.0  # New moon (wrapping around)
        elif 0.4 < moon_phase < 0.6:
            return 0.9  # Around full moon
        elif 0.2 < moon_phase < 0.3 or 0.7 < moon_phase < 0.8:
            return 0.6  # Quarter moons
        else:
            return 0.7  # In between

    def _score_season(self, current_time: datetime) -> float:
        """Score based on seasonal activity (0-1)."""
        if not self.species_profile or not current_time:
            return 0.5

        try:
            current_month = current_time.month
        except AttributeError:
            return 0.5

        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        
        if not active_months:
            return 0.7

        if current_month in active_months:
            return 1.0
        else:
            # Check if we're close to active season
            try:
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
            except (ValueError, TypeError):
                return 0.2

    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure (0-1)."""
        try:
            pressure = float(pressure)
        except (ValueError, TypeError):
            return 0.5

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