"""Marine data fetcher for Open-Meteo Marine API."""
import logging
from datetime import datetime, timedelta
import aiohttp
from homeassistant.util import dt as dt_util

from .const import OPEN_METEO_MARINE_URL

_LOGGER = logging.getLogger(__name__)


class MarineDataFetcher:
    """Fetch marine weather data from Open-Meteo."""

    def __init__(self, hass, latitude, longitude):
        """Initialize the marine data fetcher."""
        self.hass = hass
        self.latitude = latitude
        self.longitude = longitude
        self._last_fetch = None
        self._cache = None

    async def get_marine_data(self):
        """Fetch current and forecast marine data."""
        now = dt_util.now()
        
        # Cache for 1 hour
        if (self._last_fetch and self._cache and 
            (now - self._last_fetch).total_seconds() < 3600):
            return self._cache

        try:
            data = await self._fetch_from_api()
            self._cache = data
            self._last_fetch = now
            return data
        except Exception as e:
            _LOGGER.error(f"Error fetching marine data: {e}")
            return self._get_fallback_data()

    async def _fetch_from_api(self):
        """Fetch data from Open-Meteo Marine API."""
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": [
                "wave_height",
                "wave_direction",
                "wave_period",
                "wind_wave_height",
                "wind_wave_direction",
                "wind_wave_period",
                "swell_wave_height",
                "swell_wave_direction",
                "swell_wave_period",
            ],
            "timezone": "auto",
            "forecast_days": 7,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(OPEN_METEO_MARINE_URL, params=params) as response:
                if response.status != 200:
                    raise Exception(f"API returned status {response.status}")
                
                data = await response.json()
                return self._parse_marine_data(data)

    def _parse_marine_data(self, raw_data):
        """Parse the API response into usable format."""
        hourly = raw_data.get("hourly", {})
        times = hourly.get("time", [])
        
        if not times:
            return self._get_fallback_data()

        # Convert times to datetime objects
        parsed_times = [datetime.fromisoformat(t) for t in times]
        now = dt_util.now()
        
        # Find current hour index
        current_index = 0
        for i, t in enumerate(parsed_times):
            if t <= now:
                current_index = i
            else:
                break

        # Extract current conditions
        current = {
            "wave_height": hourly.get("wave_height", [None])[current_index],
            "wave_period": hourly.get("wave_period", [None])[current_index],
            "wave_direction": hourly.get("wave_direction", [None])[current_index],
            "wind_wave_height": hourly.get("wind_wave_height", [None])[current_index],
            "wind_wave_period": hourly.get("wind_wave_period", [None])[current_index],
            "swell_wave_height": hourly.get("swell_wave_height", [None])[current_index],
            "swell_wave_period": hourly.get("swell_wave_period", [None])[current_index],
            "timestamp": parsed_times[current_index],
        }

        # Build forecast (next 7 days, daily summary)
        forecast = self._build_daily_forecast(hourly, parsed_times)

        return {
            "current": current,
            "forecast": forecast,
            "source": "open-meteo",
            "last_updated": now,
        }

    def _build_daily_forecast(self, hourly, times):
        """Build daily forecast from hourly data."""
        forecast = {}
        
        for i, time in enumerate(times):
            date_key = time.date().isoformat()
            
            if date_key not in forecast:
                forecast[date_key] = {
                    "wave_heights": [],
                    "wave_periods": [],
                    "wind_wave_heights": [],
                    "swell_wave_heights": [],
                }
            
            # Collect hourly values for daily aggregation
            if hourly.get("wave_height", [None])[i] is not None:
                forecast[date_key]["wave_heights"].append(hourly["wave_height"][i])
            if hourly.get("wave_period", [None])[i] is not None:
                forecast[date_key]["wave_periods"].append(hourly["wave_period"][i])
            if hourly.get("wind_wave_height", [None])[i] is not None:
                forecast[date_key]["wind_wave_heights"].append(hourly["wind_wave_height"][i])
            if hourly.get("swell_wave_height", [None])[i] is not None:
                forecast[date_key]["swell_wave_heights"].append(hourly["swell_wave_height"][i])

        # Calculate daily statistics
        daily_forecast = {}
        for date_key, values in forecast.items():
            daily_forecast[date_key] = {
                "wave_height_max": max(values["wave_heights"]) if values["wave_heights"] else None,
                "wave_height_avg": sum(values["wave_heights"]) / len(values["wave_heights"]) if values["wave_heights"] else None,
                "wave_height_min": min(values["wave_heights"]) if values["wave_heights"] else None,
                "wave_period_avg": sum(values["wave_periods"]) / len(values["wave_periods"]) if values["wave_periods"] else None,
                "wind_wave_height_max": max(values["wind_wave_heights"]) if values["wind_wave_heights"] else None,
                "swell_wave_height_max": max(values["swell_wave_heights"]) if values["swell_wave_heights"] else None,
            }

        return daily_forecast

    def _get_fallback_data(self):
        """Return fallback data when API fails."""
        return {
            "current": {
                "wave_height": None,
                "wave_period": None,
                "wave_direction": None,
                "wind_wave_height": None,
                "wind_wave_period": None,
                "swell_wave_height": None,
                "swell_wave_period": None,
                "timestamp": dt_util.now(),
            },
            "forecast": {},
            "source": "unavailable",
            "last_updated": dt_util.now(),
        }

    def get_current_wave_height(self):
        """Get current wave height in meters."""
        if self._cache and self._cache.get("current"):
            return self._cache["current"].get("wave_height")
        return None

    def get_current_wave_period(self):
        """Get current wave period in seconds."""
        if self._cache and self._cache.get("current"):
            return self._cache["current"].get("wave_period")
        return None

    def get_wave_condition_score(self, max_wave_height=2.0):
        """
        Score wave conditions (0-100).
        
        Returns higher scores for moderate waves, lower for calm or rough.
        """
        wave_height = self.get_current_wave_height()
        
        if wave_height is None:
            return 50  # Neutral score when data unavailable
        
        if wave_height > max_wave_height:
            # Too rough - dangerous
            return 0
        elif wave_height < 0.3:
            # Too calm - less active fish
            return 60
        elif 0.5 <= wave_height <= 1.5:
            # Ideal conditions - active surf zone
            return 100
        elif 0.3 <= wave_height < 0.5:
            # Slightly calm but okay
            return 80
        else:
            # Getting rough but still fishable
            penalty = (wave_height - 1.5) / (max_wave_height - 1.5)
            return int(100 - (penalty * 100))

    def is_safe_conditions(self, max_wave_height=2.0):
        """Check if current wave conditions are safe."""
        wave_height = self.get_current_wave_height()
        
        if wave_height is None:
            return True  # Assume safe if no data
        
        return wave_height <= max_wave_height
