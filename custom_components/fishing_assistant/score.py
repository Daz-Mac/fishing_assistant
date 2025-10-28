"""Freshwater fishing scoring algorithm with strict validation and explicit failures.

This scorer now performs strict validation of inputs and species configuration.
Missing required data or invalid species profiles will raise clear exceptions
so failures surface immediately instead of being silently tolerated.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Any

from homeassistant.util import dt as dt_util

from .base_scorer import BaseScorer
from .species_loader import SpeciesLoader
from .data_formatter import DataFormatter

_LOGGER = logging.getLogger(__name__)


class FreshwaterFishingScorer(BaseScorer):
    """Freshwater fishing scoring implementation that fails loudly on missing data."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        species: List[str],
        species_profiles: Dict[str, Any],
        body_type: Optional[str] = None,
        species_loader: Optional[SpeciesLoader] = None,
    ):
        """Initialize the freshwater scorer.

        This constructor enforces that a species list is provided and that a valid
        species profile exists for the primary species. If a profile cannot be
        found or loaded, an exception is raised.
        """
        super().__init__(latitude, longitude, species, species_profiles)

        if not species:
            raise ValueError("FreshwaterFishingScorer requires a non-empty species list.")

        self.species_name = species[0]
        self.body_type = body_type or "lake"
        self.species_loader = species_loader

        # Load species profile and require it to exist
        self.species_profile: Dict[str, Any] = {}
        # Prefer provided species_profiles dict; attempt to load if missing
        if species_profiles and self.species_name in species_profiles:
            prof = species_profiles[self.species_name]
            if not isinstance(prof, dict) or not prof:
                raise ValueError(f"Species profile for '{self.species_name}' is invalid or empty.")
            self.species_profile = prof
        elif self.species_loader:
            prof = self.species_loader.get_species(self.species_name)
            if not isinstance(prof, dict) or not prof:
                raise ValueError(f"Species loader could not return a valid profile for '{self.species_name}'.")
            self.species_profile = prof
            # Persist to provided map if possible
            if isinstance(species_profiles, dict):
                species_profiles[self.species_name] = prof
        else:
            raise ValueError(
                f"Species profile for '{self.species_name}' not found and no species_loader provided."
            )

    def calculate_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Calculate the fishing score.

        Strict validation:
        - weather_data must be a dict with required fields (temperature, wind_speed, pressure, cloud_cover).
        - astro_data must be a dict (can be empty but presence is preferred for accurate time scoring).
        Any validation or processing error is raised to surface issues immediately.
        """
        if not isinstance(weather_data, dict):
            raise TypeError("weather_data must be a dict")

        if astro_data is None:
            astro_data = {}
        elif not isinstance(astro_data, dict):
            raise TypeError("astro_data must be a dict or None")

        # Delegate main calculations to parent which will call _calculate_base_score etc.
        result = super().calculate_score(
            weather_data, astro_data, tide_data, marine_data, current_time
        )

        # Parent must return a dict-type result
        if not isinstance(result, dict):
            raise TypeError("Parent scorer returned non-dict result")

        # If the provider embedded a forecast, format it strictly (exceptions will propagate)
        if "forecast" in weather_data:
            forecast_raw = weather_data["forecast"]
            if not isinstance(forecast_raw, list):
                raise TypeError("Embedded forecast must be a list of forecast entries")
            # This will raise on invalid entries instead of skipping
            formatted = self._format_forecast(forecast_raw, astro_data or {})
            if formatted:
                result["forecast"] = formatted

        return result

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Calculate component scores with strict validation.

        Required weather fields: temperature, wind_speed, pressure, cloud_cover.
        Any missing or unparsable required field will raise an exception.
        """
        # Normalize input structures
        if not isinstance(weather_data, dict):
            raise TypeError("_calculate_base_score expects weather_data as dict")
        if astro_data is None:
            astro_data = {}
        elif not isinstance(astro_data, dict):
            raise TypeError("_calculate_base_score expects astro_data as dict or None")

        # Normalize values using DataFormatter (should raise or return typed values)
        weather = DataFormatter.format_weather_data(weather_data)
        astro = DataFormatter.format_astro_data(astro_data)

        # Validate presence of required numeric fields
        missing = [k for k in ("temperature", "wind_speed", "pressure", "cloud_cover") if weather.get(k) is None]
        if missing:
            raise ValueError(f"Missing required weather fields for scoring: {missing}")

        # Normalize/validate current_time
        if current_time is None:
            current_time = dt_util.now()
        else:
            if isinstance(current_time, datetime):
                if current_time.tzinfo is None:
                    current_time = current_time.replace(tzinfo=timezone.utc)
                current_time = dt_util.as_utc(current_time)
            else:
                # try coercion for common timestamp types
                coerced = self._coerce_datetime(current_time)
                if coerced is None:
                    raise ValueError("current_time provided but could not be coerced to datetime")
                current_time = coerced

        components: Dict[str, float] = {}

        # Temperature Score
        components["temperature"] = self._normalize_score(self._score_temperature(weather["temperature"]))

        # Wind Score
        wind_speed = weather.get("wind_speed")
        wind_gust = weather.get("wind_gust", wind_speed)
        components["wind"] = self._normalize_score(self._score_wind(wind_speed, wind_gust))

        # Pressure Score
        components["pressure"] = self._normalize_score(self._score_pressure(weather["pressure"]))

        # Cloud Cover Score
        components["clouds"] = self._normalize_score(self._score_cloud_cover(weather["cloud_cover"]))

        # Time of Day Score (requires astro data for accurate scoring)
        components["time"] = self._normalize_score(self._score_time_of_day(current_time, astro))

        # Season Score
        components["season"] = self._normalize_score(self._score_season(current_time))

        # Moon Phase Score
        moon_phase = None
        if isinstance(astro, dict):
            moon_phase = astro.get("moon_phase") if "moon_phase" in astro else astro.get("moon")
        components["moon"] = self._normalize_score(self._score_moon(moon_phase))

        return components

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
        """Format forecast data strictly. Any invalid entry raises an exception.

        Each forecast entry must be a dict and include a parseable datetime and
        the required weather fields (temperature, wind_speed, pressure, cloud_cover).
        """
        if not isinstance(forecast_data, list):
            raise TypeError("forecast_data must be a list")

        formatted_forecast: List[Dict[str, Any]] = []

        for entry in forecast_data:
            if not isinstance(entry, dict):
                raise TypeError(f"Forecast entry must be a dict: {entry!r}")

            # Determine timestamp from common keys
            dt = self._coerce_datetime(entry.get("datetime") or entry.get("time") or entry.get("timestamp"))
            if not dt:
                raise ValueError(f"Forecast entry missing or has unparseable datetime: {entry}")

            # Accept alternative naming used by various sources but require the core fields
            temperature = entry.get("temperature") or entry.get("temp") or entry.get("temperature_2m") or entry.get("t")
            wind_speed = entry.get("wind_speed") or entry.get("wind") or entry.get("windspeed")
            wind_gust = entry.get("wind_gust") or entry.get("windspeed_gusts") or entry.get("gust") or wind_speed
            pressure = entry.get("pressure") or entry.get("mslp") or entry.get("pressure_hpa")
            cloud_cover = entry.get("cloud_cover") or entry.get("clouds") or entry.get("cloudcover")
            precipitation = entry.get("precipitation") or entry.get("precip") or entry.get("pop") or 0
            humidity = entry.get("humidity") or entry.get("rh") or None

            # Build a small weather block and normalize via DataFormatter
            forecast_weather_raw = {
                "temperature": temperature,
                "wind_speed": wind_speed,
                "wind_gust": wind_gust,
                "pressure": pressure,
                "precipitation": precipitation,
                "cloud_cover": cloud_cover,
                "humidity": humidity,
                "datetime": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            # Normalize numerics/datetime (will raise on invalid inputs if DataFormatter enforces types)
            forecast_weather = DataFormatter.format_weather_data(forecast_weather_raw)

            # Validate required fields after formatting
            missing = [k for k in ("temperature", "wind_speed", "pressure", "cloud_cover") if forecast_weather.get(k) is None]
            if missing:
                raise ValueError(f"Forecast entry at {dt.isoformat()} missing required fields: {missing}")

            astro_for_entry = entry.get("astro") or astro_data or {}
            if not isinstance(astro_for_entry, dict):
                raise TypeError("astro entry in forecast must be a dict if provided")

            # Calculate component scores (pass dt as current_time)
            component_scores = self._calculate_base_score(
                forecast_weather, astro_for_entry, None, None, dt
            )

            # Calculate final score with weights
            weights = self._get_factor_weights()
            score = self._weighted_average(component_scores, weights)

            # Ensure component scores are numeric and presentable
            normalized_scores = {key: round(float(value), 1) for key, value in component_scores.items()}

            formatted_forecast.append(
                {
                    "datetime": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "score": round(float(score), 1),
                    "temperature": forecast_weather.get("temperature"),
                    "wind_speed": forecast_weather.get("wind_speed"),
                    "pressure": forecast_weather.get("pressure"),
                    "component_scores": normalized_scores,
                }
            )

        return formatted_forecast

    async def calculate_forecast(
        self,
        weather_forecast: List[Dict[str, Any]],
        tide_forecast: Optional[List[Dict[str, Any]]] = None,
        marine_forecast: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Calculate fishing scores for forecast periods strictly.

        Any invalid forecast entry will cause an exception so problems are visible.
        """
        if weather_forecast is None:
            raise ValueError("weather_forecast must be provided for calculate_forecast")

        if not isinstance(weather_forecast, list):
            raise TypeError("weather_forecast must be a list")

        forecast_scores: List[Dict[str, Any]] = []

        for weather_data in weather_forecast:
            if not isinstance(weather_data, dict):
                raise TypeError("Each weather_forecast entry must be a dict")

            forecast_time = self._coerce_datetime(
                weather_data.get("datetime") or weather_data.get("time") or weather_data.get("timestamp")
            )
            if not forecast_time:
                raise ValueError(f"Forecast item missing parseable time: {weather_data}")

            astro_data = weather_data.get("astro", {}) or {}
            if not isinstance(astro_data, dict):
                raise TypeError("Forecast item's astro must be a dict if provided")

            score_result = self.calculate_score(
                weather_data=weather_data,
                astro_data=astro_data,
                tide_data=None,
                marine_data=None,
                current_time=forecast_time,
            )

            if not isinstance(score_result, dict):
                raise TypeError("calculate_score returned non-dict in forecast calculation")

            score_result["datetime"] = forecast_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            forecast_scores.append(score_result)

        return forecast_scores

    def _score_temperature(self, temperature: float) -> float:
        """Score based on temperature with species-specific range handling.

        Raises ValueError on unparsable temperature or invalid species profile temp range.
        """
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            raise ValueError("temperature value is not numeric")

        temp_range = (
            self.species_profile.get("temp_range")
            or self.species_profile.get("temperature_range")
        )
        if temp_range is None:
            raise ValueError(f"Species profile '{self.species_name}' missing 'temp_range' or 'temperature_range'")

        if not (isinstance(temp_range, (list, tuple)) and len(temp_range) == 2):
            raise ValueError(f"Species profile temp_range invalid for '{self.species_name}': {temp_range!r}")

        try:
            min_temp = float(temp_range[0])
            max_temp = float(temp_range[1])
        except (ValueError, TypeError):
            raise ValueError("temp_range entries must be numeric")

        temp_span = max_temp - min_temp
        if temp_span <= 0:
            raise ValueError("temp_range max must be greater than min")

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

    def _score_wind(self, wind_speed: float, wind_gust: float) -> float:
        """Score based on wind conditions. Requires numeric inputs."""
        try:
            wind_speed = float(wind_speed)
        except (ValueError, TypeError):
            raise ValueError("wind_speed value is not numeric")
        try:
            wind_gust = float(wind_gust)
        except (ValueError, TypeError):
            # fallback: set gust == wind_speed if gust unparsable, but still explicit
            wind_gust = wind_speed

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
            raise ValueError("pressure value is not numeric")

        prefers_low = bool(self.species_profile.get("prefers_low_pressure", False))

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
            raise ValueError("cloud_cover value is not numeric")

        if "ideal_cloud" not in self.species_profile:
            raise ValueError(f"Species profile '{self.species_name}' missing 'ideal_cloud'")

        try:
            ideal_cloud = float(self.species_profile.get("ideal_cloud"))
        except (ValueError, TypeError):
            raise ValueError("ideal_cloud in species profile must be numeric")

        cloud_diff = abs(cloud_cover - ideal_cloud)

        if cloud_diff <= 15:
            return 10.0
        elif cloud_diff <= 30:
            return 7.0
        else:
            return 4.0

    def _score_moon(self, moon_phase: Optional[float]) -> float:
        """Score based on moon phase. If moon_phase is None, raise to surface missing data."""
        if moon_phase is None:
            raise ValueError("moon_phase missing; explicit moon data required for moon scoring")
        try:
            moon_phase = float(moon_phase)
            moon_phase = max(0.0, min(1.0, moon_phase))
        except (ValueError, TypeError):
            raise ValueError("moon_phase value is not numeric")

        # New moon (0) and full moon (~0.5) are typically good
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 9.0
        elif 0.4 < moon_phase < 0.6:
            return 9.0
        else:
            return 6.0

    def _score_time_of_day(self, current_time: datetime, astro: Dict[str, Any]) -> float:
        """Score based on time of day. Requires astro with parseable sunrise/sunset."""
        if not isinstance(current_time, datetime):
            raise TypeError("current_time must be a datetime for time-of-day scoring")

        if not isinstance(astro, dict) or not astro:
            raise ValueError("Astro data required for time-of-day scoring (sunrise/sunset)")

        sunrise = astro.get("sunrise")
        sunset = astro.get("sunset")
        if not sunrise or not sunset:
            raise ValueError("Astro data must include 'sunrise' and 'sunset' for time scoring")

        sunrise_dt = self._coerce_datetime(sunrise)
        sunset_dt = self._coerce_datetime(sunset)
        if sunrise_dt is None or sunset_dt is None:
            raise ValueError("Could not parse sunrise/sunset times into datetimes")

        # Normalize to UTC
        if sunrise_dt.tzinfo is None:
            sunrise_dt = sunrise_dt.replace(tzinfo=timezone.utc)
        if sunset_dt.tzinfo is None:
            sunset_dt = sunset_dt.replace(tzinfo=timezone.utc)
        sunrise_dt = dt_util.as_utc(sunrise_dt)
        sunset_dt = dt_util.as_utc(sunset_dt)

        # Normalize current_time to UTC
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        current_time = dt_util.as_utc(current_time)

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

    def _score_season(self, current_time: datetime) -> float:
        """Score based on season/active months. Requires a valid 'active_months' list in species profile."""
        if not isinstance(current_time, datetime):
            raise TypeError("current_time must be datetime for season scoring")

        try:
            month = dt_util.as_local(current_time).month
        except Exception:
            raise ValueError("Could not determine local month from current_time")

        active_months = self.species_profile.get("active_months")
        if not isinstance(active_months, (list, tuple)):
            raise ValueError(f"Species profile '{self.species_name}' must provide 'active_months' as list/tuple")

        if month in active_months:
            return 10.0
        else:
            return 3.0

    @staticmethod
    def _coerce_datetime(v: Any) -> Optional[datetime]:
        """Coerce various timestamp types (datetime, ISO string, epoch) into timezone-aware datetime (UTC) or return None."""
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

        # Try parsing numeric epochs
        try:
            if isinstance(v, (int, float)):
                val = float(v)
                # ms vs s heuristic
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