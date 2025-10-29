"""Ocean fishing scoring algorithm with improved defensive parsing and logging.

This version hardens datetime handling (timezone-aware via Home Assistant dt_util),
defensive parsing of input shapes (forecast, astro, tide, marine), and aligns
behavior with the "fail loudly" / clear-error-attribute policy: when required
marine or tide data is missing the scorer returns an explicit error message
(instead of silently producing a default score) while still being defensive
for non-critical data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Any, Tuple

from homeassistant.util import dt as dt_util

from .base_scorer import BaseScorer
from .const import (
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
    CONF_MARINE_ENABLED,
    CONF_TIDE_MODE,
    TIDE_MODE_PROXY,
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
        species_profiles: Dict[str, Any],
        hass: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the scorer."""
        super().__init__(latitude, longitude, species, species_profiles)

        self.hass = hass
        self.config = config or {}
        self.species_loader = SpeciesLoader(hass) if hass else None
        self.species_profile: Dict[str, Any] = {}
        self._initialized = False

        # astro cache may be either:
        #  - dict keyed by ISO date strings -> astro dict (preferred), or
        #  - list of astro dicts with a 'date' key (legacy)
        self._astro_forecast_cache: Optional[Any] = None
        self._astro_cache_time: Optional[datetime] = None

    async def async_initialize(self) -> None:
        """Initialize the scorer asynchronously (load profiles, prefetch astro).

        This method is strict: if a requested species profile cannot be located,
        initialization raises an error so calling code knows configuration is invalid.
        """
        if self._initialized:
            return

        if not self.species:
            _LOGGER.error("No species configured for OceanFishingScorer")
            raise RuntimeError("OceanFishingScorer requires at least one species id")

        try:
            if self.species_loader:
                await self.species_loader.async_load_profiles()

            species_id = self.species[0]

            # Require a real species profile — do not silently fall back.
            if self.species_loader:
                profile = self.species_loader.get_species(species_id)
                if not profile:
                    _LOGGER.error("Species profile '%s' not found during OceanFishingScorer initialization", species_id)
                    raise RuntimeError(f"Species profile '{species_id}' not found")
                self.species_profile = profile
                # Update species_profiles dict for BaseScorer
                try:
                    self.species_profiles[species_id] = self.species_profile
                except Exception:
                    _LOGGER.debug("Unable to update species_profiles cache for %s", species_id)
            else:
                # If no loader available, require species_profiles to contain the profile
                profile = self.species_profiles.get(species_id)
                if not profile:
                    _LOGGER.error("No species_loader and no species_profiles entry for %s", species_id)
                    raise RuntimeError(f"Missing species profile for {species_id}")
                self.species_profile = profile

            # Pre-load astronomical forecast if hass is available; failures here are logged but non-fatal
            if self.hass:
                await self._refresh_astro_cache()

            self._initialized = True

        except Exception:
            _LOGGER.exception("Error initializing ocean scorer - aborting initialization")
            raise

    async def _refresh_astro_cache(self) -> None:
        """Refresh astronomical forecast cache (best-effort)."""
        try:
            if self.latitude is None or self.longitude is None or not self.hass:
                _LOGGER.debug("No coordinates or hass unavailable; cannot refresh astro cache")
                self._astro_forecast_cache = None
                self._astro_cache_time = None
                return

            _LOGGER.debug("Refreshing astronomical forecast cache for lat=%s lon=%s", self.latitude, self.longitude)
            cache = await calculate_astronomy_forecast(self.hass, self.latitude, self.longitude, days=7)
            self._astro_forecast_cache = cache
            self._astro_cache_time = dt_util.now()
            size = len(cache) if cache is not None and hasattr(cache, "__len__") else 0
            _LOGGER.debug("Astronomical cache refreshed with %d entries", size)
        except Exception:
            _LOGGER.exception("Error refreshing astro cache")
            self._astro_forecast_cache = None
            self._astro_cache_time = None

    def calculate_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Calculate the fishing score for a single time period.

        Instead of allowing low-level exceptions to propagate for common mis-config
        cases (missing marine/tide), this method returns a structured result that
        contains either the calculated score or a clear 'error' field and a
        'forecast_raw' breakdown that helps debug why the score is what it is.
        """

        result: Dict[str, Any] = {"score": None, "component_scores": None, "forecast_raw": None}

        # Validate weather_data shape
        if not weather_data or not isinstance(weather_data, dict):
            _LOGGER.error("OceanFishingScorer requires non-empty weather_data dict")
            result["error"] = "Missing or invalid weather_data for ocean scoring"
            return result

        # If config requires marine data, ensure it is present — return structured error
        if self.config.get(CONF_MARINE_ENABLED, True) and marine_data is None:
            _LOGGER.error("Marine data required by configuration but not provided to OceanFishingScorer")
            result["error"] = "Missing required marine data"
            return result

        # If config uses tide proxy, require tide_data presence
        if self.config.get(CONF_TIDE_MODE) == TIDE_MODE_PROXY and tide_data is None:
            _LOGGER.error("Tide proxy configured but tide_data not provided to OceanFishingScorer")
            result["error"] = "Missing required tide data for proxy tide mode"
            return result

        # Attempt to compute component scores defensively and produce a rich breakdown
        try:
            # Format the inputs for stable shapes (DataFormatter provides normalization)
            formatted_weather = DataFormatter.format_weather_data(weather_data or {})
            formatted_astro = DataFormatter.format_astro_data(astro_data or {})
            formatted_tide = DataFormatter.format_tide_data(tide_data) if tide_data else None
            formatted_marine = DataFormatter.format_marine_data(marine_data) if marine_data else None

            # Compute raw component scores (0..10). _calculate_base_score also formats defensively,
            # so we can pass formatted inputs OR original; keep using formatted to be explicit.
            component_scores = self._calculate_base_score(formatted_weather, formatted_astro, formatted_tide, formatted_marine, current_time)

            # Compute final weighted score (0..10)
            weights = self._get_factor_weights()
            score_0_10 = self._weighted_average(component_scores, weights)

            # Normalized 0..100 score for frontend
            score_0_100 = round(score_0_10 * 10.0, 1)

            # Build a readable breakdown for debugging / forecast_raw
            forecast_raw = {
                "datetime": None,
                "raw_weather": weather_data,
                "formatted_weather": formatted_weather,
                "astro_used": formatted_astro,
                "tide_used": formatted_tide,
                "marine_used": formatted_marine,
                "component_scores": component_scores,  # raw 0..10 per factor
                "component_weights": weights,
                "score_0_10": round(score_0_10, 2),
                "score_0_100": score_0_100,
            }

            # Attach datetime if we can coerce one
            dt_obj = self._coerce_datetime(current_time or weather_data.get("datetime") or weather_data.get("time") or weather_data.get("timestamp"))
            forecast_raw["datetime"] = dt_util.as_utc(dt_obj).isoformat() if dt_obj else None

            # Fill result with both raw breakdown and normalized values for compatibility
            result["component_scores"] = component_scores
            result["score"] = round(score_0_10, 2)
            result["score_100"] = score_0_100
            result["forecast_raw"] = forecast_raw

            # Merge in any summary from parent implementation (keeps backwards compatibility)
            try:
                parent_result = super().calculate_score(weather_data, astro_data, tide_data, marine_data, current_time)
                # parent_result may contain things like species-specific adjustments; prefer parent's keys
                if isinstance(parent_result, dict):
                    # preserve computed fields but allow parent to override if it has extra info
                    parent_result.update(result)
                    return parent_result
            except Exception:
                # If parent calculation fails we already have a defensively computed result; log and continue
                _LOGGER.debug("Parent BaseScorer.calculate_score failed; using local computed result", exc_info=True)

            return result

        except RuntimeError as e:
            # This likely arises from missing-but-required data in lower-level scoring.
            _LOGGER.error("Ocean scoring failed due to configuration/data error: %s", str(e))
            result["error"] = str(e)
            # Provide a minimal forecast_raw to aid debugging
            result["forecast_raw"] = {
                "raw_weather": weather_data,
                "astro": astro_data,
                "tide": tide_data,
                "marine": marine_data,
                "error": str(e),
            }
            return result
        except Exception as e:
            _LOGGER.exception("Unexpected error during ocean scoring")
            result["error"] = f"Unexpected error: {e}"
            result["forecast_raw"] = {
                "raw_weather": weather_data,
                "astro": astro_data,
                "tide": tide_data,
                "marine": marine_data,
                "error": str(e),
            }
            return result

    def _calculate_base_score(
        self,
        weather_data: Dict[str, Any],
        astro_data: Dict[str, Any],
        tide_data: Optional[Dict[str, Any]] = None,
        marine_data: Optional[Dict[str, Any]] = None,
        current_time: Optional[Any] = None,
    ) -> Dict[str, float]:
        """Calculate component scores with defensive parsing and logging.
        Raises RuntimeError when critical pieces are missing per configuration.
        """
        # Defensive formatting using DataFormatter (ensures stable shapes)
        weather = DataFormatter.format_weather_data(weather_data or {})
        astro = DataFormatter.format_astro_data(astro_data or {})
        tide = DataFormatter.format_tide_data(tide_data) if tide_data else None
        marine = DataFormatter.format_marine_data(marine_data) if marine_data else None

        # If formatting produced no useful weather, fail loudly
        if not weather:
            _LOGGER.error("Formatted weather data is empty; cannot calculate ocean base score")
            raise RuntimeError("Empty formatted weather data")

        # Respect configuration requirements: if marine is required but missing, raise
        if self.config.get(CONF_MARINE_ENABLED, True) and marine is None:
            _LOGGER.error("Configuration requires marine data but none provided to _calculate_base_score")
            raise RuntimeError("Missing marine data")

        if current_time is None:
            current_time = dt_util.now()
        else:
            parsed = self._coerce_datetime(current_time)
            current_time = parsed or dt_util.now()

        components: Dict[str, float] = {}

        # Temperature Score
        temp = weather.get("temperature")
        if temp is None:
            _LOGGER.debug("Temperature missing from weather data; using neutral score")
            components["temperature"] = 5.0
        else:
            components["temperature"] = self._normalize_score(self._score_temperature(temp))

        # Wind Score
        wind_speed = weather.get("wind_speed")
        wind_gust = weather.get("wind_gust") if weather.get("wind_gust") is not None else wind_speed
        if wind_speed is None:
            _LOGGER.debug("Wind speed missing from weather data; using neutral wind score")
            components["wind"] = 5.0
        else:
            components["wind"] = self._normalize_score(self._score_wind(wind_speed, wind_gust))

        # Pressure Score
        pressure = weather.get("pressure")
        if pressure is None:
            _LOGGER.debug("Pressure missing from weather data; using neutral pressure score")
            components["pressure"] = 5.0
        else:
            components["pressure"] = self._normalize_score(self._score_pressure(pressure))

        # Tide Score
        if tide:
            tide_state = tide.get("state", "unknown")
            tide_strength_raw = tide.get("strength", None)
            try:
                tide_strength = max(0.0, min(1.0, float(tide_strength_raw))) if tide_strength_raw is not None else 0.5
            except Exception:
                tide_strength = 0.5
            components["tide"] = self._normalize_score(self._score_tide(tide_state, tide_strength))
        else:
            # If tide data is required by config, raise; otherwise neutral but logged
            if self.config.get(CONF_TIDE_MODE) == TIDE_MODE_PROXY:
                _LOGGER.error("Tide proxy mode configured but tide data missing in _calculate_base_score")
                raise RuntimeError("Missing tide data for proxy tide mode")
            _LOGGER.debug("Tide data missing, using neutral tide score")
            components["tide"] = 5.0

        # Wave Score (marine.current may be present)
        if marine and isinstance(marine, dict):
            current_marine = marine.get("current") or {}
            # DataFormatter uses 'wave_height' key
            wave_height = current_marine.get("wave_height", current_marine.get("swell_wave_height"))
            if wave_height is None:
                _LOGGER.debug("Wave height missing in marine.current; using neutral waves score")
                components["waves"] = 5.0
            else:
                components["waves"] = self._normalize_score(self._score_waves(wave_height))
        else:
            if self.config.get(CONF_MARINE_ENABLED, True):
                _LOGGER.error("Marine data required but missing when scoring waves")
                raise RuntimeError("Missing marine data for scoring waves")
            _LOGGER.debug("Marine data missing, using neutral wave score")
            components["waves"] = 5.0

        # Time of Day Score
        components["time"] = self._normalize_score(self._score_time_of_day(current_time, astro))

        # Season Score
        components["season"] = self._normalize_score(self._score_season(current_time))

        # Moon Phase Score
        moon_phase = None
        if isinstance(astro, dict):
            moon_phase = astro.get("moon_phase") or astro.get("moon")
        components["moon"] = self._normalize_score(self._score_moon(moon_phase))

        return components

    def _get_factor_weights(self) -> Dict[str, float]:
        """Get factor weights for scoring (tunable)."""
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
        tide_data: Optional[List[Dict[str, Any]]] = None,
        marine_data: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Format forecast data for frontend compatibility with tolerant parsing.

        For each forecast step we provide:
         - datetime
         - score (0..10)
         - score_100 (0..100)
         - component_scores (raw 0..10)
         - component_scores_100 (0..100)
         - forecast_raw: includes raw inputs used in calculation and any error messages
        """
        formatted_forecast: List[Dict[str, Any]] = []

        for entry in (forecast_data or []):
            try:
                dt = self._coerce_datetime(entry.get("datetime") or entry.get("time") or entry.get("timestamp"))
                if not dt:
                    _LOGGER.debug("Skipping forecast entry with unparseable datetime: %s", entry)
                    continue

                # Tolerant extraction of common weather fields
                temperature = entry.get("temperature") or entry.get("temp") or entry.get("temperature_2m") or entry.get(
                    "t"
                )
                wind_speed = entry.get("wind_speed") or entry.get("wind") or entry.get("windspeed")
                wind_gust = entry.get("wind_gust") or entry.get("windspeed_gusts")
                pressure = entry.get("pressure") or entry.get("mslp") or entry.get("pressure_hpa")
                precipitation = entry.get("precipitation") or entry.get("precip") or entry.get("pop")
                cloud_cover = entry.get("cloud_cover") or entry.get("clouds") or entry.get("cloudcover")
                humidity = entry.get("humidity") or entry.get("rh") or None

                # Coerce wind numeric value for internal scoring
                try:
                    ws_val = float(wind_speed) if wind_speed is not None else None
                except Exception:
                    ws_val = None

                forecast_weather = {
                    "temperature": temperature,
                    "wind_speed": ws_val,
                    "wind_gust": wind_gust if wind_gust is not None else (ws_val * 1.5 if ws_val else None),
                    "pressure": pressure,
                    "precipitation": precipitation,
                    "cloud_cover": cloud_cover,
                    "humidity": humidity,
                }

                # Determine astro for this forecast time if astro_data is a cache
                astro_for_entry = astro_data or {}
                try:
                    if self._astro_forecast_cache:
                        astro_for_entry = self._find_astro_for_time(dt) or astro_for_entry
                except Exception:
                    _LOGGER.debug("Error finding astro entry for forecast time", exc_info=True)

                # Determine matching tide/marine snapshots if provided
                tide_for_entry = None
                marine_for_entry = None
                try:
                    if tide_data:
                        tide_for_entry = self._find_tide_for_time(tide_data, dt)
                    if marine_data:
                        marine_for_entry = self._find_marine_for_time(marine_data, dt)
                except Exception:
                    _LOGGER.debug("Error finding tide/marine entry for forecast time", exc_info=True)

                # Attempt to compute per-factor component scores and final score; capture errors
                try:
                    component_scores = self._calculate_base_score(forecast_weather, astro_for_entry, tide_for_entry, marine_for_entry, dt)
                    weights = self._get_factor_weights()
                    score_0_10 = self._weighted_average(component_scores, weights)
                    score_0_100 = round(score_0_10 * 10.0, 1)

                    # Convert component scores to 0..100 for frontend display
                    component_scores_100 = {k: round(float(v) * 10.0, 1) for k, v in component_scores.items()}

                    forecast_entry = {
                        "datetime": dt_util.as_utc(dt).isoformat(),
                        "score": round(score_0_10, 2),
                        "score_100": score_0_100,
                        "temperature": temperature,
                        "wind_speed": ws_val,
                        "pressure": pressure,
                        "component_scores": component_scores,
                        "component_scores_100": component_scores_100,
                        "forecast_raw": {
                            "raw_input": entry,
                            "formatted_weather": forecast_weather,
                            "astro_used": astro_for_entry,
                            "tide_used": tide_for_entry,
                            "marine_used": marine_for_entry,
                            "weights": weights,
                        },
                    }
                except RuntimeError as e:
                    # Missing critical data per configuration; provide a clear failure entry
                    _LOGGER.error("Forecast scoring failed for %s due to missing data: %s", dt, str(e))
                    forecast_entry = {
                        "datetime": dt_util.as_utc(dt).isoformat(),
                        "score": None,
                        "score_100": None,
                        "temperature": temperature,
                        "wind_speed": ws_val,
                        "pressure": pressure,
                        "component_scores": None,
                        "component_scores_100": None,
                        "forecast_raw": {
                            "raw_input": entry,
                            "formatted_weather": forecast_weather,
                            "astro_used": astro_for_entry,
                            "tide_used": tide_for_entry,
                            "marine_used": marine_for_entry,
                            "error": str(e),
                        },
                    }
                except Exception:
                    _LOGGER.exception("Error calculating forecast component scores for entry: %s", entry)
                    forecast_entry = {
                        "datetime": dt_util.as_utc(dt).isoformat(),
                        "score": None,
                        "score_100": None,
                        "temperature": temperature,
                        "wind_speed": ws_val,
                        "pressure": pressure,
                        "component_scores": None,
                        "component_scores_100": None,
                        "forecast_raw": {
                            "raw_input": entry,
                            "formatted_weather": forecast_weather,
                            "astro_used": astro_for_entry,
                            "tide_used": tide_for_entry,
                            "marine_used": marine_for_entry,
                            "error": "Unexpected error during scoring",
                        },
                    }

                formatted_forecast.append(forecast_entry)

            except Exception:
                _LOGGER.warning("Error formatting forecast entry: %s. Entry=%s", entry, exc_info=True)
                continue

        return formatted_forecast

    async def calculate_forecast(
        self,
        weather_forecast: List[Dict[str, Any]],
        tide_forecast: Optional[List[Dict[str, Any]]] = None,
        marine_forecast: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Calculate fishing scores for forecast periods, aligning astro/tide/marine data.

        Returns a list of detailed dicts. Each entry will include either a computed
        score and a 'forecast_raw' breakdown, or an 'error' field if required data
        was missing or something went wrong.
        """
        forecast_scores: List[Dict[str, Any]] = []

        # Ensure astro cache is fresh (1 hour TTL)
        try:
            if self._astro_forecast_cache is None or (
                self._astro_cache_time and (dt_util.now() - self._astro_cache_time).total_seconds() > 3600
            ):
                await self._refresh_astro_cache()
        except Exception:
            _LOGGER.debug("Error checking/refreshing astro cache", exc_info=True)

        for weather_data in (weather_forecast or []):
            try:
                forecast_time = self._coerce_datetime(weather_data.get("datetime") or weather_data.get("time") or weather_data.get("timestamp"))
                if not forecast_time:
                    _LOGGER.debug("Skipping forecast item with no parseable time: %s", weather_data)
                    continue

                # Find matching astro/tide/marine entries
                astro_data = self._find_astro_for_time(forecast_time) or {}
                tide_data_item = self._find_tide_for_time(tide_forecast, forecast_time) if tide_forecast else None
                marine_data_item = self._find_marine_for_time(marine_forecast, forecast_time) if marine_forecast else None

                score_result = self.calculate_score(
                    weather_data=weather_data,
                    astro_data=astro_data,
                    tide_data=tide_data_item,
                    marine_data=marine_data_item,
                    current_time=forecast_time,
                )

                score_result["datetime"] = dt_util.as_utc(forecast_time).isoformat()
                forecast_scores.append(score_result)

            except Exception:
                _LOGGER.exception("Error calculating forecast score for entry: %s", weather_data)
                # create an error entry to keep alignment with input
                try:
                    dt = self._coerce_datetime(weather_data.get("datetime") or weather_data.get("time") or weather_data.get("timestamp"))
                    forecast_scores.append(
                        {
                            "datetime": dt_util.as_utc(dt).isoformat() if dt else None,
                            "score": None,
                            "error": "Unhandled exception while scoring (see logs)",
                            "forecast_raw": {"raw_input": weather_data},
                        }
                    )
                except Exception:
                    # if even that fails, append a minimal placeholder
                    forecast_scores.append({"datetime": None, "score": None, "error": "Unhandled exception while scoring"})

        return forecast_scores

    def _find_astro_for_time(self, target_time: Any) -> Dict[str, Any]:
        """Find astronomical data for a specific time.

        Supports dict keyed by ISO date strings and legacy list-of-dicts.
        Returns a parsed astro dict or empty dict.
        """
        if not self._astro_forecast_cache or not target_time:
            return {}

        tgt = self._coerce_datetime(target_time)
        if not tgt:
            return {}

        try:
            target_date_iso = dt_util.as_local(tgt).date().isoformat()
        except Exception:
            target_date_iso = str(tgt)

        cache = self._astro_forecast_cache

        # If cache is a dict keyed by ISO dates, lookup directly
        if isinstance(cache, dict):
            astro_entry = cache.get(target_date_iso) or cache.get(target_date_iso + "T00:00:00") or cache.get(target_date_iso + "Z")
            if astro_entry:
                return self._parse_astro_entry(astro_entry)
            # fallback: nearest — choose first available
            try:
                first_key = sorted(cache.keys())[0]
                return self._parse_astro_entry(cache[first_key])
            except Exception:
                return {}

        # If cache is a list of dicts (legacy), search for matching 'date' key
        if isinstance(cache, list):
            for astro_data in cache:
                if not isinstance(astro_data, dict):
                    continue
                astro_date = astro_data.get("date") or astro_data.get("day") or astro_data.get("iso_date")
                if astro_date is None:
                    continue
                # Normalize candidate
                try:
                    parsed = dt_util.parse_datetime(str(astro_date))
                    if parsed and dt_util.as_local(parsed).date().isoformat() == target_date_iso:
                        return self._parse_astro_entry(astro_data)
                except Exception:
                    # fallback string compare
                    if str(astro_date) == target_date_iso:
                        return self._parse_astro_entry(astro_data)
            try:
                return self._parse_astro_entry(cache[0]) if cache else {}
            except Exception:
                return {}

        return {}

    def _parse_astro_entry(self, astro_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Parse astro entry, converting ISO strings to timezone-aware datetimes where possible."""
        if not isinstance(astro_entry, dict):
            return {}

        result: Dict[str, Any] = dict(astro_entry)  # shallow copy

        def _ensure_dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return dt_util.as_utc(v) if v.tzinfo else v.replace(tzinfo=timezone.utc)
            try:
                parsed = dt_util.parse_datetime(str(v))
                if parsed:
                    return dt_util.as_utc(parsed)
            except Exception:
                pass
            # Try parsing date-only
            try:
                if isinstance(v, str) and len(v) == 10 and "-" in v:
                    d = datetime.strptime(v, "%Y-%m-%d")
                    return d.replace(tzinfo=timezone.utc)
            except Exception:
                pass
            return None

        for key in ("sunrise", "sunset", "moonrise", "moonset", "moon_transit", "moon_underfoot"):
            if key in result:
                parsed = _ensure_dt(result.get(key))
                if parsed:
                    result[key] = parsed

        # Normalize moon phase values
        if "moon_phase" in result:
            try:
                result["moon_phase"] = float(result["moon_phase"]) if result["moon_phase"] is not None else None
            except Exception:
                result["moon_phase"] = None

        if "moon" in result and "moon_phase" not in result:
            try:
                result["moon_phase"] = float(result.get("moon"))
            except Exception:
                pass

        return result

    def _find_tide_for_time(self, tide_forecast: Optional[List[Dict[str, Any]]], target_time: Any) -> Optional[Dict[str, Any]]:
        """Find tide data closest to target time (tolerant to shapes)."""
        if not tide_forecast or not target_time:
            return None

        tgt = self._coerce_datetime(target_time)
        if not tgt:
            return None

        tgt_utc = dt_util.as_utc(tgt)
        closest_tide = None
        min_diff = float("inf")

        for item in tide_forecast:
            try:
                if not isinstance(item, dict):
                    continue
                tide_time = item.get("datetime") or item.get("time") or item.get("timestamp") or item.get("ts")
                tide_dt = self._coerce_datetime(tide_time)
                if not tide_dt:
                    continue
                tide_dt_utc = dt_util.as_utc(tide_dt)
                diff = abs((tide_dt_utc - tgt_utc).total_seconds())
                if diff < min_diff:
                    min_diff = diff
                    closest_tide = item
            except Exception:
                continue

        return closest_tide

    def _find_marine_for_time(self, marine_forecast: Optional[List[Dict[str, Any]]], target_time: Any) -> Optional[Dict[str, Any]]:
        """Find marine data closest to target time."""
        if not marine_forecast or not target_time:
            return None

        tgt = self._coerce_datetime(target_time)
        if not tgt:
            return None

        tgt_utc = dt_util.as_utc(tgt)
        closest_marine = None
        min_diff = float("inf")

        for item in marine_forecast:
            try:
                if not isinstance(item, dict):
                    continue
                marine_time = item.get("datetime") or item.get("time") or item.get("timestamp") or item.get("ts")
                marine_dt = self._coerce_datetime(marine_time)
                if not marine_dt:
                    continue
                marine_dt_utc = dt_util.as_utc(marine_dt)
                diff = abs((marine_dt_utc - tgt_utc).total_seconds())
                if diff < min_diff:
                    min_diff = diff
                    closest_marine = item
            except Exception:
                continue

        return closest_marine

    def _coerce_datetime(self, v: Any) -> Optional[datetime]:
        """Coerce various timestamp types into timezone-aware datetime (UTC) or return None."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return dt_util.as_utc(v) if v.tzinfo else v.replace(tzinfo=timezone.utc)
        # Try Home Assistant parser for many ISO-like variants
        try:
            parsed = dt_util.parse_datetime(str(v))
            if parsed:
                return dt_util.as_utc(parsed)
        except Exception:
            pass
        # Numeric epoch (seconds or milliseconds)
        try:
            if isinstance(v, (int, float)):
                val = float(v)
                # heuristics: >1e12 treat as ms
                if val > 1e12:
                    val = val / 1000.0
                return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            pass
        # Date-only string fallback YYYY-MM-DD
        try:
            s = str(v)
            if "T" not in s and len(s) == 10 and "-" in s:
                d = datetime.strptime(s, "%Y-%m-%d")
                return d.replace(tzinfo=timezone.utc)
        except Exception:
            pass

        return None

    def _score_temperature(self, temperature: Any) -> float:
        """Score based on temperature (ocean less sensitive)."""
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            return 5.0

        if 10 <= temperature <= 25:
            return 10.0
        elif 5 <= temperature <= 30:
            return 7.0
        else:
            return 5.0

    def _score_wind(self, wind_speed: Any, wind_gust: Any) -> float:
        """Score based on wind conditions."""
        try:
            wind_speed = float(wind_speed or 0)
        except (ValueError, TypeError):
            wind_speed = 0.0
        try:
            wind_gust = float(wind_gust or wind_speed)
        except (ValueError, TypeError):
            wind_gust = wind_speed

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

    def _score_pressure(self, pressure: Any) -> float:
        """Score based on barometric pressure."""
        try:
            pressure = float(pressure)
        except (ValueError, TypeError):
            return 5.0

        if 1013 <= pressure <= 1020:
            return 10.0
        elif 1008 <= pressure < 1013:
            return 8.0
        elif 1020 < pressure <= 1025:
            return 7.0
        elif 1000 <= pressure < 1008:
            return 6.0
        elif pressure > 1025:
            return 5.0
        else:
            return 4.0

    def _score_tide(self, tide_state: str, tide_strength: float) -> float:
        """Score based on tide conditions and species preference."""
        try:
            best_tide = self.species_profile.get("best_tide", "moving")
        except Exception:
            best_tide = "moving"

        try:
            tide_strength = float(tide_strength)
            tide_strength = max(0.0, min(1.0, tide_strength))
        except Exception:
            tide_strength = 0.5

        if best_tide == "any":
            return 8.0
        elif best_tide == "moving":
            if tide_state in [TIDE_STATE_RISING, TIDE_STATE_FALLING]:
                return min(10.0, 7.0 + (tide_strength * 3.0))
            else:
                return 5.0
        elif best_tide == "rising":
            if tide_state == TIDE_STATE_RISING:
                return min(10.0, 8.0 + (tide_strength * 2.0))
            else:
                return 5.0
        elif best_tide == "falling":
            if tide_state == TIDE_STATE_FALLING:
                return min(10.0, 8.0 + (tide_strength * 2.0))
            else:
                return 5.0
        elif best_tide == "slack":
            if tide_state in [TIDE_STATE_SLACK_HIGH, TIDE_STATE_SLACK_LOW]:
                return 9.0
            else:
                return 5.0
        elif best_tide == "slack_high":
            return 10.0 if tide_state == TIDE_STATE_SLACK_HIGH else 5.0
        elif best_tide == "slack_low":
            return 10.0 if tide_state == TIDE_STATE_SLACK_LOW else 5.0

        return 5.0

    def _score_waves(self, wave_height: Any) -> float:
        """Score based on wave conditions with species preferences."""
        try:
            wave_height = max(0.0, float(wave_height))
        except (ValueError, TypeError):
            return 5.0

        if not self.species_profile:
            return 5.0

        wave_pref = self.species_profile.get("wave_preference", "moderate")
        wave_bonus = self.species_profile.get("wave_bonus", False)

        score = 5.0
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

        if wave_bonus and wave_height > 1.0:
            score = min(10.0, score + 2.0)

        return score

    def _score_cloud_cover(self, cloud_cover: Any) -> float:
        """Score based on cloud cover and species cloud preference."""
        try:
            cloud_bonus = float(self.species_profile.get("cloud_bonus", 0.5) if self.species_profile else 0.5)
            cloud_bonus = max(0.0, min(1.0, cloud_bonus))
            cloud_cover = float(cloud_cover) if cloud_cover is not None else 0.0
            cloud_cover = max(0.0, min(100.0, cloud_cover))
        except (ValueError, TypeError):
            return 5.0

        # Map cloud_cover (0..100) to an additive score [0..5] scaled by cloud_bonus
        return max(0.0, min(10.0, 5.0 + (cloud_cover / 100.0 * cloud_bonus * 5.0)))

    def _score_moon(self, moon_phase: Optional[Any]) -> float:
        """Score based on moon phase (0..1)."""
        if moon_phase is None:
            return 5.0
        try:
            moon_phase = float(moon_phase)
            moon_phase = max(0.0, min(1.0, moon_phase))
        except Exception:
            return 5.0

        # New moon (0) and full moon (~0.5) are often best
        if moon_phase < 0.1 or moon_phase > 0.9:
            return 10.0  # New moon
        if 0.4 < moon_phase < 0.6:
            return 9.0  # Full moon
        if 0.2 < moon_phase < 0.3 or 0.7 < moon_phase < 0.8:
            return 6.0  # Quarter moons
        return 7.0  # In between

    def _score_time_of_day(self, current_time: Any, astro: Dict[str, Any]) -> float:
        """Score based on time of day and species light preference."""
        try:
            if not current_time:
                current_time = dt_util.now()
            else:
                coerced = self._coerce_datetime(current_time)
                current_time = coerced or dt_util.now()

            light_condition = self._determine_light_condition(astro or {}, current_time)

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

            return score_map.get(str(light_pref), {}).get(light_condition, 5.0)
        except Exception:
            _LOGGER.exception("Error scoring time of day")
            return 5.0

    def _score_season(self, current_time: Any) -> float:
        """Score based on season/active months in species profile."""
        try:
            if not current_time:
                current_time = dt_util.now()
            else:
                coerced = self._coerce_datetime(current_time)
                current_time = coerced or dt_util.now()

            current_month = dt_util.as_local(current_time).month
        except Exception:
            return 5.0

        active_months = self.species_profile.get("active_months", list(range(1, 13)))
        if not active_months:
            return 7.0

        try:
            if current_month in active_months:
                return 10.0
            # distance-to-season heuristic
            months_to_season = min(
                (abs(current_month - m) if abs(current_month - m) <= 6 else 12 - abs(current_month - m))
                for m in active_months
            )
            if months_to_season == 1:
                return 6.0
            if months_to_season == 2:
                return 4.0
            return 2.0
        except Exception:
            return 2.0

    def _determine_light_condition(self, astro_data: Dict[str, Any], current_time: Any = None) -> str:
        """Determine light condition for a specific time with fallbacks."""
        try:
            if not current_time:
                current_time = dt_util.now()
            else:
                coerced = self._coerce_datetime(current_time)
                current_time = coerced or dt_util.now()

            # Ensure current_time is timezone-aware UTC for comparisons
            current_utc = dt_util.as_utc(current_time)

            if not astro_data:
                return self._fallback_light_condition(dt_util.as_local(current_utc))

            sunrise = astro_data.get("sunrise")
            sunset = astro_data.get("sunset")

            def _ensure_dt(v):
                if v is None:
                    return None
                if isinstance(v, datetime):
                    return dt_util.as_utc(v)
                try:
                    parsed = dt_util.parse_datetime(str(v))
                    if parsed:
                        return dt_util.as_utc(parsed)
                except Exception:
                    pass
                # Fallback: parse HH:MM relative to current date as UTC
                try:
                    s = str(v)
                    if ":" in s:
                        parts = s.split(":")
                        hour = int(parts[0])
                        minute = int(parts[1]) if len(parts) > 1 else 0
                        base = dt_util.as_local(current_utc)
                        dt_val = datetime(base.year, base.month, base.day, hour, minute, tzinfo=timezone.utc)
                        return dt_util.as_utc(dt_val)
                except Exception:
                    pass
                return None

            sunrise_dt = _ensure_dt(sunrise)
            sunset_dt = _ensure_dt(sunset)

            if not sunrise_dt or not sunset_dt:
                return self._fallback_light_condition(dt_util.as_local(current_utc))

            # Dawn/dusk windows +/- 30 minutes
            dawn_start = sunrise_dt - timedelta(minutes=30)
            dawn_end = sunrise_dt + timedelta(minutes=30)
            dusk_start = sunset_dt - timedelta(minutes=30)
            dusk_end = sunset_dt + timedelta(minutes=30)

            if dawn_start <= current_utc <= dawn_end:
                return LIGHT_DAWN
            if dusk_start <= current_utc <= dusk_end:
                return LIGHT_DUSK
            if sunrise_dt < current_utc < sunset_dt:
                return LIGHT_DAY
            return LIGHT_NIGHT
        except Exception:
            _LOGGER.exception("Error determining light condition")
            return LIGHT_DAY

    def _fallback_light_condition(self, current_time: Any) -> str:
        """Fallback light condition based on hour of day (local)."""
        try:
            hour = getattr(current_time, "hour", None)
            if hour is None:
                hour = dt_util.now().hour
            if 6 <= hour < 8:
                return LIGHT_DAWN
            if 8 <= hour < 18:
                return LIGHT_DAY
            if 18 <= hour < 20:
                return LIGHT_DUSK
            return LIGHT_NIGHT
        except Exception:
            return LIGHT_DAY

    def check_safety(self, weather_data: Optional[Dict[str, Any]], marine_data: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
        """Assess safety for fishing, returning ('safe'|'caution'|'unsafe'|'unknown', reasons).

        This method now requires a valid habitat preset in config; missing or invalid presets
        will raise so configuration errors are visible.
        """
        if not weather_data and not marine_data:
            return "unknown", ["Insufficient data to assess safety"]

        habitat_preset = (self.config or {}).get(CONF_HABITAT_PRESET)
        if not habitat_preset:
            _LOGGER.error("check_safety requires a habitat preset in scorer config")
            raise RuntimeError("Missing habitat preset in configuration")

        habitat = HABITAT_PRESETS.get(habitat_preset)
        if not habitat:
            _LOGGER.error("Invalid habitat preset '%s' in config", habitat_preset)
            raise RuntimeError(f"Invalid habitat preset: {habitat_preset}")

        # Defensive extraction with fallbacks and coercion
        def _float_or_zero(v):
            try:
                return float(v)
            except Exception:
                return 0.0

        wind_speed = _float_or_zero((weather_data or {}).get("wind_speed", 0))
        wind_gust = _float_or_zero((weather_data or {}).get("wind_gust", wind_speed))
        wave_height = _float_or_zero(((marine_data or {}).get("current") or {}).get("wave_height", 0))
        precipitation = _float_or_zero((weather_data or {}).get("precipitation_probability") or (weather_data or {}).get("pop", 0))

        max_wind = _float_or_zero(habitat.get("max_wind_speed", 30))
        max_gust = _float_or_zero(habitat.get("max_gust_speed", 45))
        max_wave = _float_or_zero(habitat.get("max_wave_height", 2.5))

        reasons: List[str] = []
        unsafe_count = 0
        caution_count = 0

        # Wind speed
        if wind_speed > max_wind:
            reasons.append(f"High wind: {round(wind_speed)} km/h (max: {max_wind})")
            unsafe_count += 1
        elif wind_speed > max_wind * 0.8:
            reasons.append(f"Strong wind: {round(wind_speed)} km/h (caution at {round(max_wind * 0.8)})")
            caution_count += 1

        # Gusts
        if wind_gust > max_gust:
            reasons.append(f"Dangerous gusts: {round(wind_gust)} km/h (max: {max_gust})")
            unsafe_count += 1
        elif wind_gust > max_gust * 0.8:
            reasons.append(f"Strong gusts: {round(wind_gust)} km/h (caution at {round(max_gust * 0.8)})")
            caution_count += 1

        # Waves
        if wave_height > max_wave:
            reasons.append(f"High waves: {round(wave_height, 1)}m (max: {max_wave}m)")
            unsafe_count += 1
        elif wave_height > max_wave * 0.8:
            reasons.append(f"Large waves: {round(wave_height, 1)}m (caution at {round(max_wave * 0.8, 1)}m)")
            caution_count += 1

        # Precipitation
        if precipitation > 70:
            reasons.append(f"Heavy rain likely: {int(precipitation)}%")
            caution_count += 1
        elif precipitation > 50:
            reasons.append(f"Rain likely: {int(precipitation)}%")
            caution_count += 1

        if unsafe_count > 0:
            return "unsafe", reasons
        if caution_count > 0:
            return "caution", reasons
        return "safe", ["Conditions within safe limits"]