"""Weather data fetcher using Met.no API."""
import logging
import aiohttp
import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict

_LOGGER = logging.getLogger(__name__)

# Met.no API endpoint (same as Home Assistant uses)
METNO_API_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# User agent required by Met.no - MUST include contact info per their ToS
# Format: "AcmeWeatherApp/0.9 github.com/acmeweatherapp"
USER_AGENT = "HomeAssistant-FishingAssistant/1.0 github.com/Daz-Mac/fishing_assistant"

# Default weather values used as fallbacks when API fails or data is missing
DEFAULT_WEATHER_VALUES = {
    "temperature": 15.0,
    "wind_speed": 10.0,  # km/h
    "wind_gust": 15.0,  # km/h
    "cloud_cover": 50,  # percentage
    "precipitation_probability": 0,  # percentage
    "pressure": 1013,  # hPa
}

# Global cache to share weather data across all sensors at the same location
_GLOBAL_CACHE = {}

# Global locks to prevent multiple simultaneous requests for the same location
_FETCH_LOCKS = {}


class WeatherFetcher:
    """Fetch weather data from Met.no API for specific coordinates."""

    def __init__(self, hass, latitude: float, longitude: float):
        """Initialize the weather fetcher.
        
        Args:
            hass: Home Assistant instance
            latitude: Location latitude
            longitude: Location longitude
        """
        self.hass = hass
        # Round to max 4 decimals per Met.no ToS (5+ decimals returns 403)
        self.latitude = round(latitude, 4)
        self.longitude = round(longitude, 4)
        self._cache_key = f"{self.latitude}_{self.longitude}"
        # Cache for 2 hours to respect rate limits and avoid unnecessary traffic
        self._cache_duration = timedelta(hours=2)
        # Extended cache for 403 errors to avoid extending the block period
        self._error_cache_duration = timedelta(hours=24)
        
        # Create a lock for this location if it doesn't exist
        if self._cache_key not in _FETCH_LOCKS:
            _FETCH_LOCKS[self._cache_key] = asyncio.Lock()

    async def get_weather_data(self) -> Dict:
        """Get current weather data for the location.
        
        Returns:
            Dictionary with weather data:
            - temperature: Temperature in Celsius
            - wind_speed: Wind speed in km/h
            - wind_gust: Wind gust speed in km/h
            - cloud_cover: Cloud coverage percentage (0-100)
            - precipitation_probability: Precipitation probability percentage
            - pressure: Atmospheric pressure in hPa
        """
        # Check global cache first to avoid unnecessary traffic
        if self._cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[self._cache_key]
            cache_duration = cache_entry.get("cache_duration", self._cache_duration)
            if datetime.now() - cache_entry["time"] < cache_duration:
                _LOGGER.debug("Using cached weather data for %s, %s", self.latitude, self.longitude)
                return cache_entry["data"]

        # Use a lock to prevent multiple simultaneous requests for the same location
        async with _FETCH_LOCKS[self._cache_key]:
            # Check cache again after acquiring lock (another request may have populated it)
            if self._cache_key in _GLOBAL_CACHE:
                cache_entry = _GLOBAL_CACHE[self._cache_key]
                cache_duration = cache_entry.get("cache_duration", self._cache_duration)
                if datetime.now() - cache_entry["time"] < cache_duration:
                    _LOGGER.debug("Using cached weather data for %s, %s (from lock wait)", self.latitude, self.longitude)
                    return cache_entry["data"]

            # Add random jitter (0-30 seconds) to prevent synchronized requests
            # Per Met.no ToS: "Don't schedule many requests at the same time"
            jitter = random.uniform(0, 30)
            await asyncio.sleep(jitter)

            try:
                headers = {
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "gzip, deflate",
                }
                
                params = {
                    "lat": self.latitude,
                    "lon": self.longitude,
                }

                # Check if we have cached data with Last-Modified header
                # Per Met.no ToS: "use the If-Modified-Since request header to avoid 
                # repeatedly downloading the same data"
                if self._cache_key in _GLOBAL_CACHE:
                    last_modified = _GLOBAL_CACHE[self._cache_key].get("last_modified")
                    if last_modified:
                        headers["If-Modified-Since"] = last_modified

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        METNO_API_URL,
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        # Handle 304 Not Modified - data hasn't changed
                        if response.status == 304:
                            _LOGGER.debug("Weather data not modified for %s, %s", self.latitude, self.longitude)
                            # Update cache time but keep existing data
                            if self._cache_key in _GLOBAL_CACHE:
                                _GLOBAL_CACHE[self._cache_key]["time"] = datetime.now()
                                return _GLOBAL_CACHE[self._cache_key]["data"]
                        
                        # Handle 429 Rate Limit
                        if response.status == 429:
                            _LOGGER.error(
                                "Met.no API rate limit (429) exceeded for %s, %s. "
                                "Using cached/fallback data. Check your request frequency.",
                                self.latitude,
                                self.longitude
                            )
                            # Return cached data if available, even if expired
                            if self._cache_key in _GLOBAL_CACHE:
                                return _GLOBAL_CACHE[self._cache_key]["data"]
                            # Cache the fallback data with extended duration
                            fallback = self._get_fallback_data()
                            self._cache_fallback(fallback, extended=True)
                            return fallback
                        
                        # Handle 403 Forbidden
                        if response.status == 403:
                            _LOGGER.error(
                                "Met.no API returned 403 Forbidden for %s, %s. "
                                "Possible causes: Invalid User-Agent (must include contact info), "
                                "coordinates with >4 decimals, or ToS violation. "
                                "Caching for 24 hours to avoid extending block. "
                                "See https://api.met.no/doc/TermsOfService",
                                self.latitude,
                                self.longitude
                            )
                            # Return cached data if available, even if expired
                            if self._cache_key in _GLOBAL_CACHE:
                                # Extend cache duration to avoid repeated 403s
                                _GLOBAL_CACHE[self._cache_key]["cache_duration"] = self._error_cache_duration
                                return _GLOBAL_CACHE[self._cache_key]["data"]
                            # Cache the fallback data with extended duration (24 hours)
                            fallback = self._get_fallback_data()
                            self._cache_fallback(fallback, extended=True)
                            return fallback
                        
                        # Handle 203 Non-Authoritative (deprecated API version warning)
                        if response.status == 203:
                            _LOGGER.warning(
                                "Met.no API version is deprecated (status 203). "
                                "This version will be terminated soon. Update integration."
                            )
                        
                        if response.status not in (200, 203):
                            _LOGGER.error(
                                "Met.no API returned status %s for location %s, %s",
                                response.status,
                                self.latitude,
                                self.longitude
                            )
                            # Return cached data if available
                            if self._cache_key in _GLOBAL_CACHE:
                                return _GLOBAL_CACHE[self._cache_key]["data"]
                            # Cache the fallback data
                            fallback = self._get_fallback_data()
                            self._cache_fallback(fallback, extended=False)
                            return fallback

                        data = await response.json()
                        weather_data = self._parse_metno_data(data)
                        
                        # Store Last-Modified header for future If-Modified-Since requests
                        last_modified = response.headers.get("Last-Modified")
                        
                        # Cache the result globally with normal duration
                        _GLOBAL_CACHE[self._cache_key] = {
                            "data": weather_data,
                            "time": datetime.now(),
                            "last_modified": last_modified,
                            "cache_duration": self._cache_duration
                        }
                        
                        _LOGGER.info(
                            "Successfully fetched fresh weather data for %s, %s (cached for 2 hours)",
                            self.latitude,
                            self.longitude
                        )
                        
                        return weather_data

            except aiohttp.ClientError as e:
                _LOGGER.error("Error fetching weather from Met.no: %s", e)
                # Return cached data if available
                if self._cache_key in _GLOBAL_CACHE:
                    return _GLOBAL_CACHE[self._cache_key]["data"]
                fallback = self._get_fallback_data()
                self._cache_fallback(fallback, extended=False)
                return fallback
            except Exception as e:
                _LOGGER.error("Unexpected error fetching weather: %s", e, exc_info=True)
                # Return cached data if available
                if self._cache_key in _GLOBAL_CACHE:
                    return _GLOBAL_CACHE[self._cache_key]["data"]
                fallback = self._get_fallback_data()
                self._cache_fallback(fallback, extended=False)
                return fallback

    def _cache_fallback(self, fallback_data: Dict, extended: bool = False) -> None:
        """Cache fallback data to prevent repeated failed requests.
        
        Args:
            fallback_data: The fallback weather data to cache
            extended: If True, use extended cache duration (24 hours) for 403/429 errors
        """
        cache_duration = self._error_cache_duration if extended else self._cache_duration
        _GLOBAL_CACHE[self._cache_key] = {
            "data": fallback_data,
            "time": datetime.now(),
            "last_modified": None,
            "cache_duration": cache_duration
        }
        duration_str = "24 hours" if extended else "2 hours"
        _LOGGER.debug(
            "Cached fallback data for %s, %s for %s to prevent repeated requests", 
            self.latitude, 
            self.longitude,
            duration_str
        )

    def _parse_metno_data(self, data: Dict) -> Dict:
        """Parse Met.no API response into our weather data format.
        
        Args:
            data: Raw JSON response from Met.no API
            
        Returns:
            Parsed weather data dictionary
        """
        try:
            # Get current time series (first entry)
            timeseries = data.get("properties", {}).get("timeseries", [])
            if not timeseries:
                _LOGGER.warning("No timeseries data in Met.no response")
                return self._get_fallback_data()

            current = timeseries[0]
            instant = current.get("data", {}).get("instant", {}).get("details", {})
            next_1h = current.get("data", {}).get("next_1_hours", {}).get("details", {})

            # Extract data (Met.no uses metric units)
            # Use defaults from DEFAULT_WEATHER_VALUES if fields are missing
            wind_speed_ms = instant.get("wind_speed")
            wind_gust_ms = instant.get("wind_speed_of_gust")
            
            # If wind_gust is missing, fall back to wind_speed
            if wind_gust_ms is None:
                wind_gust_ms = wind_speed_ms
            
            # If wind_speed is missing, use default (convert from km/h to m/s for calculation)
            if wind_speed_ms is None:
                wind_speed_ms = DEFAULT_WEATHER_VALUES["wind_speed"] / 3.6
            
            if wind_gust_ms is None:
                wind_gust_ms = DEFAULT_WEATHER_VALUES["wind_gust"] / 3.6
            
            weather_data = {
                "temperature": instant.get("air_temperature", DEFAULT_WEATHER_VALUES["temperature"]),
                "wind_speed": wind_speed_ms * 3.6,  # m/s to km/h
                "wind_gust": wind_gust_ms * 3.6,  # m/s to km/h
                "cloud_cover": instant.get("cloud_area_fraction", DEFAULT_WEATHER_VALUES["cloud_cover"]),
                "precipitation_probability": next_1h.get("probability_of_precipitation", DEFAULT_WEATHER_VALUES["precipitation_probability"]),
                "pressure": instant.get("air_pressure_at_sea_level", DEFAULT_WEATHER_VALUES["pressure"]),
            }

            _LOGGER.debug(
                "Parsed weather: temp=%s°C, wind=%s km/h, clouds=%s%%",
                weather_data["temperature"],
                weather_data["wind_speed"],
                weather_data["cloud_cover"]
            )

            return weather_data

        except Exception as e:
            _LOGGER.error("Error parsing Met.no data: %s", e, exc_info=True)
            return self._get_fallback_data()

    async def get_forecast(self, days: int = 7) -> Dict[str, Dict]:
        """Get weather forecast for the location.
        
        Args:
            days: Number of days to forecast (max 10)
            
        Returns:
            Dictionary with date strings as keys and weather data as values
        """
        # Use the same cache key with a forecast suffix
        forecast_cache_key = f"{self._cache_key}_forecast_{days}"
        
        # Check cache first
        if forecast_cache_key in _GLOBAL_CACHE:
            cache_entry = _GLOBAL_CACHE[forecast_cache_key]
            cache_duration = cache_entry.get("cache_duration", self._cache_duration)
            if datetime.now() - cache_entry["time"] < cache_duration:
                _LOGGER.debug("Using cached forecast data for %s, %s", self.latitude, self.longitude)
                return cache_entry["data"]
        
        # Add random jitter to prevent synchronized requests
        jitter = random.uniform(0, 30)
        await asyncio.sleep(jitter)
        
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
            
            params = {
                "lat": self.latitude,
                "lon": self.longitude,
            }
            
            # Use If-Modified-Since if we have cached forecast data
            if forecast_cache_key in _GLOBAL_CACHE:
                last_modified = _GLOBAL_CACHE[forecast_cache_key].get("last_modified")
                if last_modified:
                    headers["If-Modified-Since"] = last_modified

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    METNO_API_URL,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    # Handle 304 Not Modified
                    if response.status == 304:
                        _LOGGER.debug("Forecast data not modified for %s, %s", self.latitude, self.longitude)
                        if forecast_cache_key in _GLOBAL_CACHE:
                            _GLOBAL_CACHE[forecast_cache_key]["time"] = datetime.now()
                            return _GLOBAL_CACHE[forecast_cache_key]["data"]
                    
                    if response.status == 203:
                        _LOGGER.warning(
                            "Met.no API version is deprecated (status 203). "
                            "This version will be terminated soon. Update integration."
                        )
                    
                    # Handle errors
                    if response.status == 403:
                        _LOGGER.error("Met.no API returned 403 for forecast request")
                        # Return cached data if available
                        if forecast_cache_key in _GLOBAL_CACHE:
                            return _GLOBAL_CACHE[forecast_cache_key]["data"]
                        return {}
                    
                    if response.status == 429:
                        _LOGGER.error("Met.no API rate limit (429) for forecast request")
                        # Return cached data if available
                        if forecast_cache_key in _GLOBAL_CACHE:
                            return _GLOBAL_CACHE[forecast_cache_key]["data"]
                        return {}
                    
                    if response.status not in (200, 203):
                        _LOGGER.error("Met.no API returned status %s for forecast", response.status)
                        # Return cached data if available
                        if forecast_cache_key in _GLOBAL_CACHE:
                            return _GLOBAL_CACHE[forecast_cache_key]["data"]
                        return {}

                    data = await response.json()
                    forecast_data = self._parse_forecast(data, days)
                    
                    # Store Last-Modified header
                    last_modified = response.headers.get("Last-Modified")
                    
                    # Cache the forecast
                    _GLOBAL_CACHE[forecast_cache_key] = {
                        "data": forecast_data,
                        "time": datetime.now(),
                        "last_modified": last_modified,
                        "cache_duration": self._cache_duration
                    }
                    
                    _LOGGER.info("Successfully fetched forecast for %s, %s", self.latitude, self.longitude)
                    
                    return forecast_data

        except Exception as e:
            _LOGGER.error("Error fetching forecast from Met.no: %s", e)
            # Return cached data if available
            if forecast_cache_key in _GLOBAL_CACHE:
                return _GLOBAL_CACHE[forecast_cache_key]["data"]
            return {}

    def _parse_forecast(self, data: Dict, days: int) -> Dict[str, Dict]:
        """Parse Met.no forecast data.
        
        Args:
            data: Raw JSON response from Met.no API
            days: Number of days to include
            
        Returns:
            Dictionary with date strings as keys
        """
        forecast = {}
        
        try:
            timeseries = data.get("properties", {}).get("timeseries", [])
            
            # Group by date and calculate daily averages
            daily_data = {}
            
            for entry in timeseries:
                time_str = entry.get("time")
                if not time_str:
                    continue
                
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                date_str = dt.date().isoformat()
                
                # Only include requested days
                if (dt.date() - datetime.now().date()).days >= days:
                    continue
                
                instant = entry.get("data", {}).get("instant", {}).get("details", {})
                next_1h = entry.get("data", {}).get("next_1_hours", {}).get("details", {})
                
                if date_str not in daily_data:
                    daily_data[date_str] = []
                
                # Use defaults for missing values
                wind_speed_ms = instant.get("wind_speed", DEFAULT_WEATHER_VALUES["wind_speed"] / 3.6)
                wind_gust_ms = instant.get("wind_speed_of_gust", wind_speed_ms)
                
                daily_data[date_str].append({
                    "temperature": instant.get("air_temperature", DEFAULT_WEATHER_VALUES["temperature"]),
                    "wind_speed": wind_speed_ms * 3.6,
                    "wind_gust": wind_gust_ms * 3.6,
                    "cloud_cover": instant.get("cloud_area_fraction", DEFAULT_WEATHER_VALUES["cloud_cover"]),
                    "precipitation_probability": next_1h.get("probability_of_precipitation", DEFAULT_WEATHER_VALUES["precipitation_probability"]),
                    "pressure": instant.get("air_pressure_at_sea_level", DEFAULT_WEATHER_VALUES["pressure"]),
                })
            
            # Calculate daily averages
            for date_str, entries in daily_data.items():
                if not entries:
                    continue
                
                # Filter out None temperatures for averaging
                temps = [e["temperature"] for e in entries if e["temperature"] is not None]
                avg_temp = sum(temps) / len(temps) if temps else DEFAULT_WEATHER_VALUES["temperature"]
                
                forecast[date_str] = {
                    "temperature": avg_temp,
                    "wind_speed": sum(e["wind_speed"] for e in entries) / len(entries),
                    "wind_gust": max(e["wind_gust"] for e in entries),
                    "cloud_cover": sum(e["cloud_cover"] for e in entries) / len(entries),
                    "precipitation_probability": max(e["precipitation_probability"] for e in entries),
                    "pressure": sum(e["pressure"] for e in entries) / len(entries),
                }
            
            return forecast
            
        except Exception as e:
            _LOGGER.error("Error parsing forecast data: %s", e, exc_info=True)
            return {}

    def _get_fallback_data(self) -> Dict:
        """Return fallback weather data when API fails.
        
        Returns:
            Dictionary with neutral/default weather values
        """
        return DEFAULT_WEATHER_VALUES.copy()