"""Freshwater fishing scoring algorithm with defensive parsing and tolerant forecast handling."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Any

from homeassistant.util import dt as dt_util

from .base_scorer import BaseScorer
from .species_loader import SpeciesLoader
from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)


class FreshwaterFishingScorer(BaseScorer):
    """Freshwater fishing scoring implementation with tolerant parsing."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: dict[str, Any],
        body_type: Optional[str] = None,
        species_loader: Optional[SpeciesLoader] = None,
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
            self.species_profile = species_loader.get_species(self.species_name) or {}
            if self.species_profile:
                self.species_profiles[self.species_name] = self.species_profile
        else:
            self.species_profile = {}

        if not self.species_profile:
            _LOGGER.warning("Species profile not found: %s", self.species_name)
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
            # Call parent calculate_score (handles formatting and final packaging)
            result = super().calculate_score(
                weather_data, astro_data, tide_data, marine_data, current_time
            )

            # If weather_data contains a forecast list, attach a formatted forecast
            if isinstance(weather_data, dict) and "forecast" in weather_data and isinstance(
                weather_data["forecast"], list
            ):
                try:
                    result["forecast"] = self._format_forecast(
                        weather_data["forecast"], astro_data
                    )
                except Exception as exc:
                    _LOGGER.debug("Failed to format embedded forecast: %s", exc, exc_info=True)

            return result

        except Exception as e:
            _LOGGER.error("Error calculating freshwater score: %s", e, exc_info=True)
            # Return default result
            return DataFormatter.format_score_result(
                {
                    "score": 5.0,
                    "conditions_summary": "Error calculating score",
                    "component_scores": {},
                    "breakdown": {},
                }
            )

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Calculate component scores with defensive parsing."""
        try:
            # Format input data defensively
            weather = DataFormatter.format_weather_data(weather_data or {})
            astro = DataFormatter.format_astro_data(astro_data or {})

            if current_time is None:
                current_time = dt_util.now()

            components: Dict[str, float] = {}

            # Temperature Score
            temp = weather.get("temperature")
            if temp is not None:
                components["temperature"] = self._normalize_score(
                    self._score_temperature(temp)
                )
            else:
                _LOGGER.debug("Temperature missing; using neutral temperature score")
                components["temperature"] = 5.0

            # Wind Score
            wind_speed = weather.get("wind_speed", 0)
            wind_gust = weather.get("wind_gust", wind_speed)
            components["wind"] = self._normalize_score(
                self._score_wind(wind_speed, wind_gust)
            )

            # Pressure Score
            pressure = weather.get("pressure", 1013)
            components["pressure"] = self._normalize_score(
                self._score_pressure(pressure)
            )

            # Cloud Cover Score
            cloud_cover = weather.get("cloud_cover", 50)
            components["clouds"] = self._normalize_score(
                self._score_cloud_cover(cloud_cover)
            )

            # Time of Day Score
            components["time"] = self._normalize_score(
                self._score_time_of_day(current_time, astro)
            )

            # Season Score
            components["season"] = self._normalize_score(
                self._score_season(current_time)
            )

            # Moon Phase Score - tolerant keys
            moon_phase = None
            if isinstance(astro, dict):
                moon_phase = astro.get("moon_phase") or astro.get("moon")
            components["moon"] = self._normalize_score(
                self._score_moon(moon_phase)
            )

            return components

        except Exception as e:
            _LOGGER.error("Error in _calculate_base_score: %s", e, exc_info=True)
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
        """Get factor weights for scoring."""
        return {
            "temperature": 0.25,
            "wind": 0.15,
            "pressure": 0.15,
            "clouds": 0.15,
            "time": 0.15,
            "season": 0.10,
            "moon": 0.05,
        }

    def _format_forecast(
        self, forecast_data: List[Dict[str, Any]], astro_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Format forecast data for frontend compatibility with tolerant parsing.

        Accepts multiple common field names and ensures datetime parsing.
        """
        formatted_forecast: List[Dict[str, Any]] = []

        for entry in (forecast_data or []):
            try:
                # Determine timestamp from common keys
                dt = self._coerce_datetime(
                    entry.get("datetime") or entry.get("time") or entry.get("timestamp")
                )
                if not dt:
                    _LOGGER.debug(
                        "Skipping forecast entry with unparseable datetime: %s", entry
                    )
                    continue

                # Accept alternative naming used by various sources (Open-Meteo, other APIs)
                temperature = (
                    entry.get("temperature")
                    or entry.get("temp")
                    or entry.get("temperature_2m")
                    or entry.get("t")
                )
                wind_speed = (
                    entry.get("wind_speed")
                    or entry.get("wind")
                    or entry.get("windspeed")
                )
                wind_gust = (
                    entry.get("wind_gust")
                    or entry.get("windspeed_gusts")
                    or None
                )
                pressure = (
                    entry.get("pressure")
                    or entry.get("mslp")
                    or entry.get("pressure_hpa")
                )
                precipitation = (
                    entry.get("precipitation")
                    or entry.get("precip")
                    or entry.get("pop")
                    or 0
                )
                cloud_cover = (
                    entry.get("cloud_cover")
                    or entry.get("clouds")
                    or entry.get("cloudcover")
                    or 0
                )
                humidity = entry.get("humidity") or entry.get("rh") or None

                # Ensure numeric conversions where sensible (DataFormatter will further coerce)
                try:
                    ws_val = float(wind_speed) if wind_speed is not None else 0.0
                except Exception:
                    ws_val = 0.0

                forecast_weather = {
                    "temperature": temperature,
                    "wind_speed": ws_val,
                    "wind_gust": wind_gust if wind_gust is not None else (ws_val * 1.5 if ws_val else 0.0),
                    "pressure": pressure,
                    "precipitation": precipitation,
                    "cloud_cover": cloud_cover,
                    "humidity": humidity,
                }

                # Use astro_data per-entry if provided, otherwise use supplied astro_data
                astro_for_entry = entry.get("astro") or astro_data or {}

                # Calculate component scores (pass dt as current_time)
                component_scores = self._calculate_base_score(
                    forecast_weather, astro_for_entry, None, None, dt
                )

                # Calculate final score with weights
                weights = self._get_factor_weights()
                score = self._weighted_average(component_scores, weights)

                # Normalize component scores for frontend (0-100)
                normalized_scores = {
                    key: round(float(value) * 10.0, 1) for key, value in component_scores.items()
                }

                formatted_forecast.append(
                    {
                        "datetime": dt.isoformat(),
                        "score": round(score, 1),
                        "temperature": temperature,
                        "wind_speed": ws_val,
                        "pressure": pressure,
                        "component_scores": normalized_scores,
                    }
                )
            except Exception as e:
                _LOGGER.warning(
                    "Error formatting forecast entry: %s. Entry=%s", e, entry, exc_info=True
                )
                continue

        return formatted_forecast

    async def calculate_forecast(
        self,
        weather_forecast: List[Dict[str, Any]],
        tide_forecast: Optional[List[Dict[str, Any]]] = None,
        marine_forecast: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Calculate fishing scores for forecast periods.

        This method is tolerant of different shapes in weather_forecast entries.
        """
        forecast_scores: List[Dict[str, Any]] = []

        for weather_data in (weather_forecast or []):
            try:
                # Coerce timestamp from multiple possible fields
                forecast_time = self._coerce_datetime(
                    weather_data.get("datetime")
                    or weather_data.get("time")
                    or weather_data.get("timestamp")
                )
                if not forecast_time:
                    _LOGGER.debug(
                        "Skipping forecast item with no parseable time: %s", weather_data
                    )
                    continue

                # Prefer astro included in the forecast entry, otherwise use provided astro
                astro_data = weather_data.get("astro", {}) or {}

                score_result = self.calculate_score(
                    weather_data=weather_data,
                    astro_data=astro_data,
                    tide_data=None,
                    marine_data=None,
                    current_time=forecast_time,
                )

                # Add timestamp to result in ISO format (ensure not to overwrite existing)
                score_result["datetime"] = forecast_time.isoformat()
                forecast_scores.append(score_result)

            except Exception as e:
                _LOGGER.error("Error calculating forecast score: %s", e, exc_info=True)
                continue

        return forecast_scores

    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature with species-specific range handling."""
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            return 5.0

        temp_range = self.species_profile.get("temp_range") or self.species_profile.get("temperature_range") or [5, 30]
        if isinstance(temp_range, (list, tuple)) and len(temp_range) == 2:
            min_temp, max_temp = temp_range
            try:
                min_temp = float(min_temp)
                max_temp = float(max_temp)
            except (ValueError, TypeError):
                return 5.0

            temp_span = max_temp - min_temp
            if temp_span <= 0:
                return 5.0

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
        try:
            wind_speed = float(wind_speed or 0)
        except (ValueError, TypeError):
            wind_speed = 0.0
        try:
            wind_gust = float(wind_gust or wind_speed)
        except (ValueError, TypeError):
            wind_gust = wind_speed

        # Simple logic (could be tuned by species/profile)
        if 5 <= wind_speed <= 15:
            return 10.0
        elif wind_speed > 25:
            return 3.0
        else:
            return 7.0

    def _score_pressure(self, pressure: float) -> float:
        """Score based on barometric pressure."""
        try:
            pressure = float(pressure)
        except (ValueError, TypeError):
            return 5.0

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
        """Score based on cloud cover relative to species preference."""
        try:
            cloud_cover = float(cloud_cover)
        except (ValueError, TypeError):
            return 5.0

        ideal_cloud = self.species_profile.get("ideal_cloud", 50)
        try:
            ideal_cloud = float(ideal_cloud)
        except (ValueError, TypeError):
            ideal_cloud = 50.0

        cloud_diff = abs(cloud_cover - ideal_cloud)

        if cloud_diff <= 15:
            return 10.0
        elif cloud_diff <= 30:
            return 7.0
        else:
            return 4.0

    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase (tolerant)."""
        if moon_phase is None:
            return 5.0
        try:
            moon_phase = float(moon_phase)
            moon_phase = max(0.0, min(1.0, moon_phase))
        except (ValueError, TypeError):
            return 5.0

        # New moon (0) and full moon (~0.5) are typically good
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 9.0
        elif 0.4 < moon_phase < 0.6:
            return 9.0
        else:
            return 6.0

    def _score_time_of_day(self, current_time: datetime, astro: Dict[str, Any]) -> float:
        """Score based on time of day with a simple fallback if astro not available."""
        try:
            # Ensure current_time is a timezone-aware datetime in UTC
            if not isinstance(current_time, datetime):
                current_time = dt_util.now()
            else:
                # If naive, assume UTC for internal comparisons
                if current_time.tzinfo is None:
                    current_time = current_time.replace(tzinfo=timezone.utc)
                current_time = dt_util.as_utc(current_time)

            # If astro has sunrise/sunset, use it; otherwise fallback to hour ranges.
            if isinstance(astro, dict):
                sunrise = astro.get("sunrise")
                sunset = astro.get("sunset")
                if sunrise and sunset:
                    sunrise_dt = self._coerce_datetime(sunrise)
                    sunset_dt = self._coerce_datetime(sunset)

                    # If sunrise/sunset were provided as time-only strings (HH:MM), _coerce_datetime
                    # may still return None; attempt to build datetimes from current date.
                    if sunrise_dt is None or sunset_dt is None:
                        try:
                            # Parse "HH:MM" formats
                            def time_only_to_dt(tstr: str) -> Optional[datetime]:
                                try:
                                    parts = str(tstr).strip().split(":")
                                    if len(parts) >= 2:
                                        hour = int(parts[0])
                                        minute = int(parts[1])
                                        dt = datetime(
                                            year=current_time.year,
                                            month=current_time.month,
                                            day=current_time.day,
                                            hour=hour,
                                            minute=minute,
                                            tzinfo=timezone.utc,
                                        )
                                        return dt
                                except Exception:
                                    return None
                                return None

                            if sunrise_dt is None:
                                sunrise_dt = time_only_to_dt(sunrise)
                            if sunset_dt is None:
                                sunset_dt = time_only_to_dt(sunset)
                        except Exception:
                            pass

                    if sunrise_dt and sunset_dt:
                        # Normalize to UTC
                        if sunrise_dt.tzinfo is None:
                            sunrise_dt = sunrise_dt.replace(tzinfo=timezone.utc)
                        if sunset_dt.tzinfo is None:
                            sunset_dt = sunset_dt.replace(tzinfo=timezone.utc)
                        sunrise_dt = dt_util.as_utc(sunrise_dt)
                        sunset_dt = dt_util.as_utc(sunset_dt)

                        # Dawn/dusk window +/- 30 minutes
                        dawn_start = sunrise_dt - timedelta(minutes=30)
                        dawn_end = sunrise_dt + timedelta(minutes=30)
                        dusk_start = sunset_dt - timedelta(minutes=30)
                        dusk_end = sunset_dt + timedelta(minutes=30)

                        if dawn_start <= current_time <= dawn_end:
                            return 10.0
                        if dusk_start <= current_time <= dusk_end:
                            return 10.0
                        if sunrise_dt < current_time < sunset_dt:
                            return 6.0
                        return 6.0

            # Fallback to basic hour checks (local hour)
            hour = dt_util.as_local(current_time).hour if current_time else datetime.now().hour
            if 5 <= hour <= 8 or 17 <= hour <= 20:
                return 10.0
            else:
                return 6.0
        except Exception:
            return 6.0

    def _score_season(self, current_time: datetime) -> float:
        """Score based on season/active months."""
        try:
            if not isinstance(current_time, datetime):
                current_time = dt_util.now()
            month = dt_util.as_local(current_time).month
        except Exception:
            return 5.0

        active_months = self.species_profile.get("active_months", list(range(1, 13)))

        if month in active_months:
            return 10.0
        else:
            return 3.0

    @staticmethod
    def _coerce_datetime(v: Any) -> Optional[datetime]:
        """Coerce various timestamp types (datetime, ISO string) into timezone-aware datetime (UTC) or return None."""
        if v is None:
            return None
        if isinstance(v, datetime):
            # Ensure timezone-aware and in UTC
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return dt_util.as_utc(v)
        # Use Home Assistant util to parse many ISO-like variants
        try:
            parsed = dt_util.parse_datetime(str(v))
            if parsed:
                return dt_util.as_utc(parsed)
        except Exception:
            pass

        # Try parsing common epoch mills/seconds
        try:
            if isinstance(v, (int, float)):
                val = float(v)
                if val > 1e12:
                    val = val / 1000.0
                return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            pass

        # Try to handle simple YYYY-MM-DD date strings
        try:
            s = str(v)
            if "T" not in s and len(s) == 10:
                # assume YYYY-MM-DD
                dt = datetime.strptime(s, "%Y-%m-%d")
                return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

        return None