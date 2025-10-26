"""Weather data fetcher - supports robust Home Assistant weather entity parsing.

This version:
- Handles multiple common forecast attribute key names and formats.
- Safely converts units (when unit indicators are present).
- Synthesizes a simple multi-day forecast when no forecast is available,
  using current conditions as a fallback (prevents empty forecast returns).
- Provides clearer logs for debugging when a chosen weather entity provides
  no forecast data.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Any

_LOGGER = logging.getLogger(__name__)

# Default weather values used as fallbacks when data is missing
DEFAULT_WEATHER_VALUES = {
    "temperature": 15.0,
    "wind_speed": 10.0,  # km/h
    "wind_gust": 15.0,  # km/h
    "cloud_cover": 50,  # percentage
    "precipitation_probability": 0,  # percentage
    "pressure": 1013,  # hPa
}

# Global cache to share weather data across all sensors
_GLOBAL_CACHE: Dict[str, Dict[str, Any]] = {}


class WeatherFetcher:
    """Fetch weather data from Home Assistant weather entity or fallback to defaults."""

    def __init__(self, hass, latitude: float, longitude: float, weather_entity: Optional[str] = None):
        """Initialize the weather fetcher.

        Args:
            hass: Home Assistant instance
            latitude: Location latitude
            longitude: Location longitude
            weather_entity: Optional HA weather entity ID (e.g., 'weather.home')
        """
        self.hass = hass
        self.latitude = round(latitude, 4)
        self.longitude = round(longitude, 4)
        self.weather_entity = weather_entity
        self._cache_key = f"{self.latitude}_{self.longitude}_{weather_entity}"
        self._cache_duration = timedelta(minutes=30)  # Cache for 30 minutes

    async def get_weather_data(self) -> Dict:
        """Get current weather data.

        Returns:
            Dictionary with weather data:
            - temperature: Celsius
            - wind_speed: km/h
            - wind_gust: km/h
            - cloud_cover: 0-100
            - precipitation_probability: 0-100
            - pressure: hPa
        """
        # Check cache first
        if self._cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[self._cache_key]
            if datetime.now(timezone.utc) - cache_entry["time"] < self._cache_duration:
                _LOGGER.debug("Using cached weather data")
                return cache_entry["data"]

        # Try to get data from HA weather entity
        if self.weather_entity:
            weather_data = await self._get_from_ha_entity()
            if weather_data:
                _GLOBAL_CACHE[self._cache_key] = {"data": weather_data, "time": datetime.now(timezone.utc)}
                _LOGGER.info("Fetched weather from HA entity: %s", self.weather_entity)
                return weather_data

        # Fallback to defaults
        _LOGGER.warning("Unable to fetch weather data from entity '%s'; using default values", self.weather_entity)
        fallback = self._get_fallback_data()
        _GLOBAL_CACHE[self._cache_key] = {"data": fallback, "time": datetime.now(timezone.utc)}
        return fallback

    async def _get_from_ha_entity(self) -> Optional[Dict]:
        """Get weather data from Home Assistant weather entity. Returns None on error."""
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return None

            attrs = state.attributes or {}

            # Common current value keys - fallbacks included
            temperature = attrs.get("temperature") or attrs.get("temp") or attrs.get("temperature_current")
            wind_speed = attrs.get("wind_speed") or attrs.get("wind")
            wind_gust = attrs.get("wind_gust_speed") or attrs.get("wind_gust") or wind_speed
            pressure = attrs.get("pressure") or attrs.get("air_pressure")
            cloud_cover = attrs.get("cloud_coverage") or attrs.get("cloud_cover") or attrs.get("clouds")
            precip_prob = 0

            # If the forecast attribute exists and includes probabilities, try to pull the first entry's precipitation probability
            try:
                forecast_list = attrs.get("forecast", [])
                if isinstance(forecast_list, list) and len(forecast_list) > 0:
                    first = forecast_list[0] if isinstance(forecast_list[0], dict) else {}
                    precip_prob = first.get("precipitation_probability", first.get("precipitation", first.get("precipitation_probability_percent", 0)) or 0)
            except Exception:
                precip_prob = 0

            # Some integrations may provide humidity instead of cloud cover — approximate
            if cloud_cover is None:
                humidity = attrs.get("humidity")
                if humidity is not None:
                    try:
                        cloud_cover = min(100, float(humidity) * 1.2)
                    except Exception:
                        cloud_cover = 50
                else:
                    cloud_cover = 50

            # Unit conversion if unit attribute present
            wind_unit = attrs.get("wind_speed_unit")
            if wind_unit and wind_unit == "m/s" and wind_speed is not None:
                try:
                    wind_speed = float(wind_speed) * 3.6
                    if wind_gust is not None:
                        wind_gust = float(wind_gust) * 3.6
                except Exception:
                    pass
            elif wind_unit and wind_unit in ("mph", "mi/h") and wind_speed is not None:
                try:
                    wind_speed = float(wind_speed) * 1.609344
                    if wind_gust is not None:
                        wind_gust = float(wind_gust) * 1.609344
                except Exception:
                    pass

            # Safe coercion to floats with defaults
            def safe_float(v, default):
                try:
                    return float(v) if v is not None else default
                except Exception:
                    return default

            weather_data = {
                "temperature": safe_float(temperature, DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": safe_float(wind_speed, DEFAULT_WEATHER_VALUES["wind_speed"]),
                "wind_gust": safe_float(wind_gust, DEFAULT_WEATHER_VALUES["wind_gust"]),
                "cloud_cover": int(safe_float(cloud_cover, DEFAULT_WEATHER_VALUES["cloud_cover"])),
                "precipitation_probability": int(safe_float(precip_prob, DEFAULT_WEATHER_VALUES["precipitation_probability"])),
                "pressure": safe_float(pressure, DEFAULT_WEATHER_VALUES["pressure"]),
            }

            _LOGGER.debug(
                "Parsed HA weather entity '%s': temp=%s°C, wind=%s km/h, gust=%s km/h, clouds=%s%%",
                self.weather_entity,
                weather_data["temperature"],
                weather_data["wind_speed"],
                weather_data["wind_gust"],
                weather_data["cloud_cover"],
            )

            return weather_data

        except Exception as exc:  # broad except to avoid integration crash
            _LOGGER.error("Error reading weather entity '%s': %s", self.weather_entity, exc, exc_info=True)
            return None

    async def get_forecast(self, days: int = 7) -> Dict[str, Dict]:
        """Get weather forecast (returns dict keyed by ISO date strings).

        If the HA weather entity provides a forecast, parse it. If not, synthesize
        a short forecast using current conditions so downstream scoring has data.
        """
        forecast_cache_key = f"{self._cache_key}_forecast_{days}"

        # Check cache
        if forecast_cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[forecast_cache_key]
            if datetime.now(timezone.utc) - cache_entry["time"] < self._cache_duration:
                _LOGGER.debug("Using cached forecast data")
                return cache_entry["data"]

        # Try HA entity forecast
        forecast_data = {}
        if self.weather_entity:
            forecast_data = await self._get_forecast_from_ha_entity(days)
            if forecast_data:
                _GLOBAL_CACHE[forecast_cache_key] = {"data": forecast_data, "time": datetime.now(timezone.utc)}
                _LOGGER.info("Fetched forecast from HA entity: %s", self.weather_entity)
                return forecast_data

        # If no forecast available, warn and synthesize a forecast from current conditions
        _LOGGER.warning(
            "Unable to fetch forecast from entity '%s' (no usable forecast found). Synthesizing %s-day forecast from current conditions.",
            self.weather_entity,
            days,
        )

        current = await self.get_weather_data()
        synthesized: Dict[str, Dict] = {}
        today = datetime.now(timezone.utc).date()
        for i in range(days):
            date_key = (today + timedelta(days=i)).isoformat()
            synthesized[date_key] = {
                "temperature": current.get("temperature", DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": current.get("wind_speed", DEFAULT_WEATHER_VALUES["wind_speed"]),
                "wind_gust": current.get("wind_gust", DEFAULT_WEATHER_VALUES["wind_gust"]),
                "cloud_cover": current.get("cloud_cover", DEFAULT_WEATHER_VALUES["cloud_cover"]),
                "precipitation_probability": current.get("precipitation_probability", DEFAULT_WEATHER_VALUES["precipitation_probability"]),
                "pressure": current.get("pressure", DEFAULT_WEATHER_VALUES["pressure"]),
            }

        _GLOBAL_CACHE[forecast_cache_key] = {"data": synthesized, "time": datetime.now(timezone.utc)}
        return synthesized

    async def _get_forecast_from_ha_entity(self, days: int) -> Dict[str, Dict]:
        """Parse a forecast attribute from a HA weather entity into a simple daily dict.

        Accepts many common key names for datetime and data fields used by HA integrations.
        """
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return {}

            attrs = state.attributes or {}
            forecast_list = attrs.get("forecast") or attrs.get("forecasts") or []

            if not isinstance(forecast_list, list) or len(forecast_list) == 0:
                _LOGGER.debug("Weather entity '%s' has no forecast list or it's empty", self.weather_entity)
                return {}

            forecast: Dict[str, Dict] = {}
            wind_unit = attrs.get("wind_speed_unit", None)

            # Common keys for time in forecast entries
            time_keys = ("datetime", "time", "from", "to", "start_time", "start", "dt", "timestamp")

            for entry in forecast_list[: days * 5]:  # allow multiple entries per day
                if not isinstance(entry, dict):
                    continue

                # Try to find a datetime string or numeric timestamp in a tolerant manner
                dt_val = None
                for k in time_keys:
                    val = entry.get(k)
                    if val:
                        dt_val = val
                        break

                if dt_val is None:
                    # Some forecast entries use nested dicts or keys like 'datetime_utc'
                    dt_val = entry.get("datetime_utc") or entry.get("date") or entry.get("date_time")

                if dt_val is None:
                    # no usable datetime found, skip
                    continue

                # Parse dt_val to a date string
                date_str = None
                try:
                    if isinstance(dt_val, (int, float)):
                        # assume unix timestamp (seconds)
                        dt = datetime.fromtimestamp(int(dt_val), tz=timezone.utc)
                    else:
                        # string: try isoformat parse, tolerate Z
                        dt = datetime.fromisoformat(str(dt_val).replace("Z", "+00:00"))
                    date_str = dt.date().isoformat()
                except Exception:
                    # Try parsing date-only strings
                    try:
                        date_only = str(dt_val).split("T")[0]
                        datetime.strptime(date_only, "%Y-%m-%d")
                        date_str = date_only
                    except Exception:
                        _LOGGER.debug("Could not parse forecast entry time '%s' for entity %s", dt_val, self.weather_entity)
                        continue

                # Helper to choose numeric fields from varied key names
                def pick_number(d: dict, keys, default):
                    for k in keys:
                        v = d.get(k)
                        if v is not None:
                            try:
                                return float(v)
                            except Exception:
                                continue
                    return default

                # Temperature keys - try to get single temp or average low/high
                temperature = pick_number(entry, ["temperature", "temp", "temp_min", "temp_max", "temperature_avg", "templow", "temperature_low", "temperature_high"], DEFAULT_WEATHER_VALUES["temperature"])
                # Wind speed keys
                wind_speed = pick_number(entry, ["wind_speed", "wind", "wind_speed_avg", "wind_kph"], DEFAULT_WEATHER_VALUES["wind_speed"])
                wind_gust = pick_number(entry, ["wind_gust_speed", "wind_gust", "wind_gusts"], wind_speed or DEFAULT_WEATHER_VALUES["wind_gust"])
                # Pressure
                pressure = pick_number(entry, ["pressure", "air_pressure"], DEFAULT_WEATHER_VALUES["pressure"])
                # Clouds / coverage
                cloud_cover = pick_number(entry, ["cloud_coverage", "cloud_cover", "clouds", "clouds_percent"], DEFAULT_WEATHER_VALUES["cloud_cover"])
                # Precip prob
                precip_prob = pick_number(entry, ["precipitation_probability", "precipitation", "pop", "probability"], DEFAULT_WEATHER_VALUES["precipitation_probability"])

                # Units conversion (if top-level attribute provided unit)
                if wind_unit == "m/s" and wind_speed is not None:
                    wind_speed = wind_speed * 3.6
                    if wind_gust is not None:
                        wind_gust = wind_gust * 3.6
                elif wind_unit in ("mph", "mi/h") and wind_speed is not None:
                    wind_speed = wind_speed * 1.609344
                    if wind_gust is not None:
                        wind_gust = wind_gust * 1.609344

                # Accumulate / average multiple entries per day
                if date_str in forecast:
                    existing = forecast[date_str]
                    count = existing.get("_count", 1) + 1
                    forecast[date_str] = {
                        "temperature": (existing["temperature"] * (count - 1) + temperature) / count,
                        "wind_speed": (existing["wind_speed"] * (count - 1) + wind_speed) / count,
                        "wind_gust": max(existing["wind_gust"], wind_gust),
                        "cloud_cover": (existing["cloud_cover"] * (count - 1) + cloud_cover) / count,
                        "precipitation_probability": max(existing["precipitation_probability"], precip_prob),
                        "pressure": (existing["pressure"] * (count - 1) + pressure) / count,
                        "_count": count,
                    }
                else:
                    forecast[date_str] = {
                        "temperature": temperature,
                        "wind_speed": wind_speed,
                        "wind_gust": wind_gust,
                        "cloud_cover": cloud_cover,
                        "precipitation_probability": precip_prob,
                        "pressure": pressure,
                        "_count": 1,
                    }

            # Trim to requested number of days, sort ascending by date
            if not forecast:
                _LOGGER.debug("Forecast list present but no usable entries found for entity '%s'", self.weather_entity)
                return {}

            sorted_dates = sorted(forecast.keys())
            final: Dict[str, Dict] = {}
            for d_key in sorted_dates[:days]:
                # remove internal _count and coerce types
                entry = forecast[d_key]
                entry.pop("_count", None)
                # safe types
                final[d_key] = {
                    "temperature": float(entry.get("temperature", DEFAULT_WEATHER_VALUES["temperature"])),
                    "wind_speed": float(entry.get("wind_speed", DEFAULT_WEATHER_VALUES["wind_speed"])),
                    "wind_gust": float(entry.get("wind_gust", DEFAULT_WEATHER_VALUES["wind_gust"])),
                    "cloud_cover": int(float(entry.get("cloud_cover", DEFAULT_WEATHER_VALUES["cloud_cover"]))),
                    "precipitation_probability": int(float(entry.get("precipitation_probability", DEFAULT_WEATHER_VALUES["precipitation_probability"]))),
                    "pressure": float(entry.get("pressure", DEFAULT_WEATHER_VALUES["pressure"])),
                }

            return final

        except Exception as exc:
            _LOGGER.error("Error parsing forecast from entity '%s': %s", self.weather_entity, exc, exc_info=True)
            return {}

    def _get_fallback_data(self) -> Dict:
        """Return fallback weather data when entity is unavailable."""
        return DEFAULT_WEATHER_VALUES.copy()