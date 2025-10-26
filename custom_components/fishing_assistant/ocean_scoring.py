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
from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)


class OceanFishingScorer(BaseScorer):
    """Calculate ocean fishing scores based on conditions and species."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: dict[str, Any],
        hass=None,
        config: Dict = None
    ):
        """Initialize the scorer."""
        super().__init__(latitude, longitude, species, species_profiles)

        self.hass = hass
        self.config = config or {}
        self.species_loader = SpeciesLoader(hass) if hass else None
        self.species_profile = None
        self._initialized = False
        # astro cache may be either:
        #  - dict keyed by ISO date strings -> astro dict (preferred), or
        #  - list of astro dicts with a 'date' key (legacy)
        self._astro_forecast_cache: Optional[Any] = None
        self._astro_cache_time: Optional[datetime] = None

    async def async_initialize(self):
        """Initialize the scorer asynchronously."""
        if self._initialized:
            return

        try:
            if self.species_loader:
                await self.species_loader.async_load_profiles()

            # Load species profile
            species_id = self.species[0] if self.species else "general_mixed"

            if self.species_loader:
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
            if self.hass:
                await self._refresh_astro_cache()

            self._initialized = True

        except Exception as e:
            _LOGGER.error("Error initializing ocean scorer: %s", e, exc_info=True)
            self.species_profile = self._get_fallback_profile()
            self._initialized = True

    async def _refresh_astro_cache(self):
        """Refresh astronomical forecast cache."""
        try:
            if self.latitude is None or self.longitude is None:
                _LOGGER.warning("No coordinates configured, using fallback astro data")
                return

            _LOGGER.info("Refreshing astronomical forecast cache")
            # calculate_astronomy_forecast returns a dict keyed by ISO date strings
            cache = await calculate_astronomy_forecast(
                self.hass,
                self.latitude,
                self.longitude,
                days=7
            )
            self._astro_forecast_cache = cache
            self._astro_cache_time = datetime.now()
            # Log numeric size (works for dict or list)
            size = len(cache) if hasattr(cache, "__len__") else 0
            _LOGGER.debug("Astronomical cache refreshed with %d entries", size)
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
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Calculate the fishing score with error handling and forecast support."""
        try:
            # Call parent calculate_score
            result = super().calculate_score(
                weather_data, astro_data, tide_data, marine_data, current_time
            )

            # Add forecast if available in weather_data
            if "forecast" in weather_data and isinstance(weather_data["forecast"], list):
                result["forecast"] = self._format_forecast(
                    weather_data["forecast"], astro_data, tide_data, marine_data
                )

            return result

        except Exception as e:
            _LOGGER.error(f"Error calculating ocean score: {e}", exc_info=True)
            # Return default result
            return DataFormatter.format_score_result({
                "score": 5.0,
                "conditions_summary": "Error calculating score",
                "component_scores": {},
                "breakdown": {}
            })

    # Implement abstract methods from BaseScorer
    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Calculate component scores with error handling."""
        try:
            # Format input data
            weather = DataFormatter.format_weather_data(weather_data)
            astro = DataFormatter.format_astro_data(astro_data)
            tide = DataFormatter.format_tide_data(tide_data) if tide_data else None
            marine = DataFormatter.format_marine_data(marine_data) if marine_data else None

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

            # Tide Score
            if tide:
                tide_state = tide.get("state", "unknown")
                tide_strength = tide.get("strength", 50) / 100.0
                components["tide"] = self._normalize_score(self._score_tide(tide_state, tide_strength))
            else:
                components["tide"] = 5.0

            # Wave Score
            if marine:
                current_marine = marine.get("current", {}) if isinstance(marine, dict) else {}
                wave_height = current_marine.get("wave_height", 1.0)
                components["waves"] = self._normalize_score(self._score_waves(wave_height))
            else:
                components["waves"] = 5.0

            # Time of Day Score
            components["time"] = self._normalize_score(self._score_time_of_day(current_time, astro))

            # Season Score
            components["season"] = self._normalize_score(self._score_season(current_time))

            # Moon Phase Score
            moon_phase = astro.get("moon")
            # Some callers place moon_phase under 'moon' or 'moon_phase'
            if moon_phase is None:
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
                "tide": 5.0,
                "waves": 5.0,
                "time": 5.0,
                "season": 5.0,
                "moon": 5.0,
            }

    def _get_factor_weights(self) -> Dict[str, float]:
        """Get factor weights for scoring."""
        return {
            "tide": 0.25,
            "wind": 0.15,
            "waves": 0.15,
            "time": 0.15,
            "pressure": 0.10,
            "season": 0.10,
            "moon": 0.05,
            "temperature": 0.03,
        }

    def _format_forecast(
        self,
        forecast_data: List[Dict[str, Any]],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Format forecast data for frontend compatibility."""
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
                    forecast_weather, astro_data, tide_data, marine_data, entry.get("datetime")
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
        """Calculate fishing scores for forecast periods."""
        forecast_scores = []

        # Ensure astro cache is fresh
        if self._astro_forecast_cache is None or (
            self._astro_cache_time and
            (datetime.now() - self._astro_cache_time).total_seconds() > 3600
        ):
            await self._refresh_astro_cache()

        for weather_data in weather_forecast:
            try:
                # Get timestamp from weather data
                forecast_time = weather_data.get("datetime")
                if not forecast_time:
                    continue

                # Find matching astro data (tolerant)
                astro_data = self._find_astro_for_time(forecast_time)

                # Find matching tide data
                tide_data = None
                if tide_forecast:
                    tide_data = self._find_tide_for_time(tide_forecast, forecast_time)

                # Find matching marine data
                marine_data = None
                if marine_forecast:
                    marine_data = self._find_marine_for_time(marine_forecast, forecast_time)

                # Calculate score
                score_result = self.calculate_score(
                    weather_data=weather_data,
                    astro_data=astro_data,
                    tide_data=tide_data,
                    marine_data=marine_data,
                    current_time=forecast_time
                )

                # Add timestamp to result
                score_result["datetime"] = forecast_time
                forecast_scores.append(score_result)

            except Exception as e:
                _LOGGER.error("Error calculating forecast score: %s", e, exc_info=True)
                continue

        return forecast_scores

    def _find_astro_for_time(self, target_time: datetime) -> Dict[str, Any]:
        """Find astronomical data for a specific time.

        Supports both dict keyed by ISO date strings and legacy list-of-dicts.
        Returns a dict with parsed datetime fields where possible.
        """
        if not self._astro_forecast_cache or not target_time:
            return {}

        # Normalized target date string
        try:
            target_date_iso = target_time.date().isoformat()
        except Exception:
            target_date_iso = str(target_time)

        cache = self._astro_forecast_cache

        # If cache is a dict keyed by ISO dates, lookup directly
        if isinstance(cache, dict):
            astro_entry = cache.get(target_date_iso)
            if astro_entry:
                return self._parse_astro_entry(astro_entry)
            # fallback: try nearest date key (first entry)
            try:
                first_key = sorted(cache.keys())[0]
                return self._parse_astro_entry(cache[first_key])
            except Exception:
                return {}

        # If cache is a list of dicts (legacy), search for matching 'date' key
        if isinstance(cache, list):
            for astro_data in cache:
                astro_date = astro_data.get("date")
                if astro_date is None:
                    continue
                # astro_date may be a date/datetime or an ISO string
                if isinstance(astro_date, str):
                    if astro_date == target_date_iso:
                        return self._parse_astro_entry(astro_data)
                else:
                    try:
                        if getattr(astro_date, "isoformat", lambda: None)() == target_date_iso:
                            return self._parse_astro_entry(astro_data)
                    except Exception:
                        continue
            # fallback to first
            return self._parse_astro_entry(cache[0]) if cache else {}

        # Unknown cache shape
        return {}

    def _parse_astro_entry(self, astro_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Return astro_entry but parse ISO datetime strings to datetime objects where possible."""
        if not isinstance(astro_entry, dict):
            return {}

        result: Dict[str, Any] = dict(astro_entry)  # shallow copy

        def _ensure_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                # Try ISO parse
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    # Try trimming timezone Z -> +00:00
                    try:
                        return datetime.fromisoformat(v.replace("Z", "+00:00"))
                    except Exception:
                        return None
            return None

        # Parse sunrise/sunset and moon event times if present
        for key in ("sunrise", "sunset", "moonrise", "moonset", "moon_transit", "moon_underfoot"):
            if key in result:
                parsed = _ensure_dt(result.get(key))
                if parsed:
                    result[key] = parsed
                # if parsing fails, leave original; caller will handle missing types

        # moon_phase might be fractional or int; coerce to float if possible
        if "moon_phase" in result:
            try:
                result["moon_phase"] = float(result["moon_phase"]) if result["moon_phase"] is not None else None
            except Exception:
                result["moon_phase"] = None

        return result

    def _find_tide_for_time(
        self, tide_forecast: List[Dict[str, Any]], target_time: datetime
    ) -> Optional[Dict[str, Any]]:
        """Find tide data closest to target time.

        This function is tolerant: tide_forecast items can be dicts with a 'datetime'
        key or simple ISO datetime strings.
        """
        if not tide_forecast:
            return None

        # helper to coerce possible string target_time
        def _coerce_dt(v):
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    try:
                        return datetime.fromisoformat(v.replace("Z", "+00:00"))
                    except Exception:
                        return None
            return None

        tgt = _coerce_dt(target_time) or target_time
        if isinstance(tgt, datetime) and tgt.tzinfo is not None:
            tgt = tgt.replace(tzinfo=None)

        closest_tide = None
        min_diff = None

        for item in tide_forecast:
            # Accept dict-like or raw timestamp strings
            tide_time = None
            tide_item = None

            if isinstance(item, dict):
                tide_item = item
                tide_time = item.get("datetime") or item.get("timestamp") or item.get("time")
            else:
                # item might be an ISO string or timestamp
                tide_time = item

            tide_dt = _coerce_dt(tide_time)
            if tide_dt is None:
                continue

            if tide_dt.tzinfo is not None:
                tide_dt = tide_dt.replace(tzinfo=None)

            try:
                time_diff = abs((tide_dt - tgt).total_seconds())
            except Exception:
                continue

            if min_diff is None or time_diff < min_diff:
                min_diff = time_diff
                # prefer returning the original dict if present
                closest_tide = tide_item if tide_item is not None else {"datetime": tide_dt}

        return closest_tide

    def _find_marine_for_time(
        self, marine_forecast: List[Dict[str, Any]], target_time: datetime
    ) -> Optional[Dict[str, Any]]:
        """Find marine data closest to target time.

        Tolerant of items that are dicts with 'datetime'/'timestamp' keys or bare ISO strings.
        Returns a dict representing the matched marine entry.
        """
        if not marine_forecast:
            return None

        # helper to coerce possible string target_time
        def _coerce_dt(v):
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    try:
                        return datetime.fromisoformat(v.replace("Z", "+00:00"))
                    except Exception:
                        return None
            return None

        tgt = _coerce_dt(target_time) or target_time
        if isinstance(tgt, datetime) and tgt.tzinfo is not None:
            tgt = tgt.replace(tzinfo=None)

        closest_marine = None
        min_diff = None

        for item in marine_forecast:
            marine_time = None
            marine_item = None

            if isinstance(item, dict):
                marine_item = item
                # common keys that may hold timestamp
                marine_time = item.get("datetime") or item.get("timestamp") or item.get("time")
            else:
                # item may just be an ISO date/time string
                marine_time = item

            marine_dt = _coerce_dt(marine_time)
            if marine_dt is None:
                # Nothing parseable for this item
                continue

            if marine_dt.tzinfo is not None:
                marine_dt = marine_dt.replace(tzinfo=None)

            try:
                time_diff = abs((marine_dt - tgt).total_seconds())
            except Exception:
                continue

            if min_diff is None or time_diff < min_diff:
                min_diff = time_diff
                # prefer returning the original dict if we have it; otherwise wrap minimal info
                closest_marine = marine_item if marine_item is not None else {"datetime": marine_dt}

        return closest_marine

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

        # If sunrise/sunset are strings, try to parse
        def _ensure_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    try:
                        return datetime.fromisoformat(v.replace("Z", "+00:00"))
                    except Exception:
                        return None
            return None

        sunrise_dt = _ensure_dt(sunrise)
        sunset_dt = _ensure_dt(sunset)

        if not sunrise_dt or not sunset_dt:
            return self._fallback_light_condition(current_time)

        try:
            # Normalize tzinfo for comparison
            if current_time.tzinfo is not None:
                current_time = current_time.replace(tzinfo=None)
            if sunrise_dt.tzinfo is not None:
                sunrise_dt = sunrise_dt.replace(tzinfo=None)
            if sunset_dt.tzinfo is not None:
                sunset_dt = sunset_dt.replace(tzinfo=None)

            # Calculate dawn and dusk periods (30 min before/after)
            dawn_start = sunrise_dt - timedelta(minutes=30)
            dawn_end = sunrise_dt + timedelta(minutes=30)
            dusk_start = sunset_dt - timedelta(minutes=30)
            dusk_end = sunset_dt + timedelta(minutes=30)

            if dawn_start <= current_time <= dawn_end:
                return LIGHT_DAWN
            elif dusk_start <= current_time <= dusk_end:
                return LIGHT_DUSK
            elif sunrise_dt < current_time < sunset_dt:
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

        habitat_preset = self.config.get(CONF_HABITAT_PRESET, "rocky_point") if self.config else "rocky_point"
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