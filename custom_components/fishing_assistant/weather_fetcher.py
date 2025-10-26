"""Weather data fetcher - supports both HA weather entities and Met.no API."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List

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
_GLOBAL_CACHE = {}


class WeatherFetcher:
    """Fetch weather data from Home Assistant weather entity or fallback to defaults."""

    def __init__(self, hass, latitude: float, longitude: float, weather_entity: Optional[str] = None):
        """Initialize the weather fetcher.
        
        Args:
            hass: Home Assistant instance
            latitude: Location latitude
            longitude: Location longitude
            weather_entity: Optional HA weather entity ID (e.g., 'weather.forecast_home')
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
            - temperature: Temperature in Celsius
            - wind_speed: Wind speed in km/h
            - wind_gust: Wind gust speed in km/h
            - cloud_cover: Cloud coverage percentage (0-100)
            - precipitation_probability: Precipitation probability percentage
            - pressure: Atmospheric pressure in hPa
        """
        # Check cache first
        if self._cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[self._cache_key]
            if datetime.now() - cache_entry["time"] < self._cache_duration:
                _LOGGER.debug("Using cached weather data")
                return cache_entry["data"]

        # Try to get data from HA weather entity
        if self.weather_entity:
            weather_data = await self._get_from_ha_entity()
            if weather_data:
                # Cache the result
                _GLOBAL_CACHE[self._cache_key] = {
                    "data": weather_data,
                    "time": datetime.now(),
                }
                _LOGGER.info("Successfully fetched weather data from HA entity: %s", self.weather_entity)
                return weather_data

        # Fallback to default values
        _LOGGER.warning(
            "Unable to fetch weather data from entity '%s', using default values",
            self.weather_entity
        )
        fallback = self._get_fallback_data()
        
        # Cache the fallback
        _GLOBAL_CACHE[self._cache_key] = {
            "data": fallback,
            "time": datetime.now(),
        }
        
        return fallback

    async def _get_from_ha_entity(self) -> Optional[Dict]:
        """Get weather data from Home Assistant weather entity.
        
        Returns:
            Weather data dictionary or None if entity not available
        """
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return None

            attrs = state.attributes
            
            # Extract data from weather entity
            # HA weather entities use different units, need to convert
            temperature = attrs.get("temperature")
            wind_speed = attrs.get("wind_speed")  # Usually in km/h or m/s
            wind_gust = attrs.get("wind_gust_speed", wind_speed)  # Fallback to wind_speed
            pressure = attrs.get("pressure")
            cloud_cover = attrs.get("cloud_coverage", 50)  # Default to 50% if not available
            
            # Some weather entities provide humidity instead of cloud cover
            if cloud_cover is None:
                humidity = attrs.get("humidity")
                if humidity is not None:
                    # Rough approximation: high humidity often means more clouds
                    cloud_cover = min(100, humidity * 1.2)
                else:
                    cloud_cover = 50
            
            # Check wind speed unit and convert if needed
            wind_speed_unit = attrs.get("wind_speed_unit", "km/h")
            if wind_speed_unit == "m/s" and wind_speed is not None:
                wind_speed = wind_speed * 3.6  # Convert m/s to km/h
                if wind_gust is not None:
                    wind_gust = wind_gust * 3.6
            elif wind_speed_unit == "mph" and wind_speed is not None:
                wind_speed = wind_speed * 1.60934  # Convert mph to km/h
                if wind_gust is not None:
                    wind_gust = wind_gust * 1.60934
            
            # Build weather data with defaults for missing values
            weather_data = {
                "temperature": temperature if temperature is not None else DEFAULT_WEATHER_VALUES["temperature"],
                "wind_speed": wind_speed if wind_speed is not None else DEFAULT_WEATHER_VALUES["wind_speed"],
                "wind_gust": wind_gust if wind_gust is not None else DEFAULT_WEATHER_VALUES["wind_gust"],
                "cloud_cover": cloud_cover,
                "precipitation_probability": attrs.get("forecast", [{}])[0].get("precipitation_probability", 0) if attrs.get("forecast") else 0,
                "pressure": pressure if pressure is not None else DEFAULT_WEATHER_VALUES["pressure"],
            }

            _LOGGER.debug(
                "Parsed HA weather entity: temp=%s°C, wind=%s km/h, clouds=%s%%",
                weather_data["temperature"],
                weather_data["wind_speed"],
                weather_data["cloud_cover"]
            )

            return weather_data

        except Exception as e:
            _LOGGER.error("Error reading weather entity '%s': %s", self.weather_entity, e, exc_info=True)
            return None

    async def get_forecast(self, days: int = 7) -> Dict[str, Dict]:
        """Get weather forecast.
        
        Args:
            days: Number of days to forecast (max 10)
            
        Returns:
            Dictionary with date strings as keys and weather data as values
        """
        forecast_cache_key = f"{self._cache_key}_forecast_{days}"
        
        # Check cache first
        if forecast_cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[forecast_cache_key]
            if datetime.now() - cache_entry["time"] < self._cache_duration:
                _LOGGER.debug("Using cached forecast data")
                return cache_entry["data"]
        
        # Try to get forecast from HA weather entity
        if self.weather_entity:
            forecast_data = await self._get_forecast_from_ha_entity(days)
            if forecast_data:
                # Cache the forecast
                _GLOBAL_CACHE[forecast_cache_key] = {
                    "data": forecast_data,
                    "time": datetime.now(),
                }
                _LOGGER.info("Successfully fetched forecast from HA entity: %s", self.weather_entity)
                return forecast_data
        
        _LOGGER.warning("Unable to fetch forecast from entity '%s'", self.weather_entity)
        return {}

    async def _get_forecast_from_ha_entity(self, days: int) -> Dict[str, Dict]:
        """Get forecast data from Home Assistant weather entity.
        
        Args:
            days: Number of days to include
            
        Returns:
            Dictionary with date strings as keys
        """
        try:
            state = self.hass.states.get(self.weather_entity)
            if not state:
                _LOGGER.error("Weather entity '%s' not found", self.weather_entity)
                return {}

            attrs = state.attributes
            forecast_list = attrs.get("forecast", [])
            
            if not forecast_list:
                _LOGGER.warning("No forecast data in weather entity '%s'", self.weather_entity)
                return {}
            
            forecast = {}
            wind_speed_unit = attrs.get("wind_speed_unit", "km/h")
            
            for entry in forecast_list[:days]:
                # Get the datetime for this forecast entry
                datetime_str = entry.get("datetime")
                if not datetime_str:
                    continue
                
                # Parse datetime and get date string
                try:
                    dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
                    date_str = dt.date().isoformat()
                except Exception as e:
                    _LOGGER.warning("Error parsing forecast datetime '%s': %s", datetime_str, e)
                    continue
                
                # Extract forecast data
                temperature = entry.get("temperature")
                wind_speed = entry.get("wind_speed")
                wind_gust = entry.get("wind_gust_speed", wind_speed)
                pressure = entry.get("pressure")
                cloud_cover = entry.get("cloud_coverage", 50)
                precip_prob = entry.get("precipitation_probability", 0)
                
                # Convert wind speed if needed
                if wind_speed_unit == "m/s" and wind_speed is not None:
                    wind_speed = wind_speed * 3.6
                    if wind_gust is not None:
                        wind_gust = wind_gust * 3.6
                elif wind_speed_unit == "mph" and wind_speed is not None:
                    wind_speed = wind_speed * 1.60934
                    if wind_gust is not None:
                        wind_gust = wind_gust * 1.60934
                
                # If we already have data for this date, average it
                if date_str in forecast:
                    existing = forecast[date_str]
                    count = existing.get("_count", 1) + 1
                    
                    forecast[date_str] = {
                        "temperature": (existing["temperature"] * (count - 1) + (temperature or DEFAULT_WEATHER_VALUES["temperature"])) / count,
                        "wind_speed": (existing["wind_speed"] * (count - 1) + (wind_speed or DEFAULT_WEATHER_VALUES["wind_speed"])) / count,
                        "wind_gust": max(existing["wind_gust"], wind_gust or DEFAULT_WEATHER_VALUES["wind_gust"]),
                        "cloud_cover": (existing["cloud_cover"] * (count - 1) + cloud_cover) / count,
                        "precipitation_probability": max(existing["precipitation_probability"], precip_prob),
                        "pressure": (existing["pressure"] * (count - 1) + (pressure or DEFAULT_WEATHER_VALUES["pressure"])) / count,
                        "_count": count,
                    }
                else:
                    forecast[date_str] = {
                        "temperature": temperature if temperature is not None else DEFAULT_WEATHER_VALUES["temperature"],
                        "wind_speed": wind_speed if wind_speed is not None else DEFAULT_WEATHER_VALUES["wind_speed"],
                        "wind_gust": wind_gust if wind_gust is not None else DEFAULT_WEATHER_VALUES["wind_gust"],
                        "cloud_cover": cloud_cover,
                        "precipitation_probability": precip_prob,
                        "pressure": pressure if pressure is not None else DEFAULT_WEATHER_VALUES["pressure"],
                        "_count": 1,
                    }
            
            # Remove the _count field from final results
            for date_str in forecast:
                forecast[date_str].pop("_count", None)
            
            return forecast
            
        except Exception as e:
            _LOGGER.error("Error parsing forecast from entity '%s': %s", self.weather_entity, e, exc_info=True)
            return {}

    def _get_fallback_data(self) -> Dict:
        """Return fallback weather data when entity is unavailable.
        
        Returns:
            Dictionary with neutral/default weather values
        """
        return DEFAULT_WEATHER_VALUES.copy()