"""Weather data fetcher using Met.no API."""
import logging
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Met.no API endpoint (same as Home Assistant uses)
METNO_API_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# User agent required by Met.no
USER_AGENT = "HomeAssistant-FishingAssistant/1.0"


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
        self.latitude = latitude
        self.longitude = longitude
        self._cache = None
        self._cache_time = None
        self._cache_duration = timedelta(minutes=30)  # Met.no updates every 30 min

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
        # Check cache
        if self._cache and self._cache_time:
            if datetime.now() - self._cache_time < self._cache_duration:
                return self._cache

        try:
            headers = {
                "User-Agent": USER_AGENT,
            }
            
            params = {
                "lat": self.latitude,
                "lon": self.longitude,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    METNO_API_URL,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "Met.no API returned status %s for location %s, %s",
                            response.status,
                            self.latitude,
                            self.longitude
                        )
                        return self._get_fallback_data()

                    data = await response.json()
                    weather_data = self._parse_metno_data(data)
                    
                    # Cache the result
                    self._cache = weather_data
                    self._cache_time = datetime.now()
                    
                    return weather_data

        except aiohttp.ClientError as e:
            _LOGGER.error("Error fetching weather from Met.no: %s", e)
            return self._get_fallback_data()
        except Exception as e:
            _LOGGER.error("Unexpected error fetching weather: %s", e, exc_info=True)
            return self._get_fallback_data()

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
            weather_data = {
                "temperature": instant.get("air_temperature"),
                "wind_speed": instant.get("wind_speed", 0) * 3.6,  # m/s to km/h
                "wind_gust": instant.get("wind_speed_of_gust", instant.get("wind_speed", 0)) * 3.6,  # m/s to km/h
                "cloud_cover": instant.get("cloud_area_fraction", 50),  # 0-100
                "precipitation_probability": next_1h.get("probability_of_precipitation", 0),
                "pressure": instant.get("air_pressure_at_sea_level", 1013),
            }

            _LOGGER.debug(
                "Fetched weather for %s, %s: temp=%s°C, wind=%s km/h, clouds=%s%%",
                self.latitude,
                self.longitude,
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
        try:
            headers = {
                "User-Agent": USER_AGENT,
            }
            
            params = {
                "lat": self.latitude,
                "lon": self.longitude,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    METNO_API_URL,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        _LOGGER.error("Met.no API returned status %s", response.status)
                        return {}

                    data = await response.json()
                    return self._parse_forecast(data, days)

        except Exception as e:
            _LOGGER.error("Error fetching forecast from Met.no: %s", e)
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
                
                daily_data[date_str].append({
                    "temperature": instant.get("air_temperature"),
                    "wind_speed": instant.get("wind_speed", 0) * 3.6,
                    "wind_gust": instant.get("wind_speed_of_gust", instant.get("wind_speed", 0)) * 3.6,
                    "cloud_cover": instant.get("cloud_area_fraction", 50),
                    "precipitation_probability": next_1h.get("probability_of_precipitation", 0),
                    "pressure": instant.get("air_pressure_at_sea_level", 1013),
                })
            
            # Calculate daily averages
            for date_str, entries in daily_data.items():
                if not entries:
                    continue
                
                forecast[date_str] = {
                    "temperature": sum(e["temperature"] for e in entries if e["temperature"]) / len([e for e in entries if e["temperature"]]),
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
        return {
            "temperature": 15.0,
            "wind_speed": 10.0,
            "wind_gust": 15.0,
            "cloud_cover": 50,
            "precipitation_probability": 0,
            "pressure": 1013,
        }