"""Astronomical tide proxy calculator."""
import logging
from datetime import datetime, timedelta
import math
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class TideProxy:
    """Calculate tide state using only Sun and Moon positions."""

    def __init__(self, hass, latitude, longitude):
        """Initialize the tide proxy."""
        self.hass = hass
        self.latitude = latitude
        self.longitude = longitude
        self._last_calculation = None
        self._cache = {}

    async def get_tide_data(self):
        """Get current tide state and predictions."""
        now = dt_util.now()
        
        # Cache for 15 minutes
        if (self._last_calculation and 
            (now - self._last_calculation).total_seconds() < 900):
            return self._cache

        moon_data = await self._get_moon_data()
        sun_data = await self._get_sun_data()
        
        tide_state = self._calculate_tide_state(moon_data, sun_data, now)
        tide_strength = self._calculate_tide_strength(moon_data)
        next_changes = self._predict_tide_changes(moon_data, now)
        
        self._cache = {
            "state": tide_state,
            "strength": tide_strength,
            "next_high": next_changes["next_high"],
            "next_low": next_changes["next_low"],
            "confidence": "proxy",
            "source": "astronomical_calculation",
        }
        
        self._last_calculation = now
        return self._cache

    async def _get_moon_data(self):
        """Get moon position and phase from HA."""
        moon_entity = self.hass.states.get("sensor.moon")
        
        # Try to get moon phase (0-1, where 0=new, 0.5=full)
        moon_phase = 0.5  # Default
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
            moon_phase = phase_map.get(phase_name, 0.5)
        
        # Calculate moon altitude (simplified - would use skyfield in production)
        # For now, use a sinusoidal approximation
        now = dt_util.now()
        hour_angle = (now.hour + now.minute / 60) / 24 * 2 * math.pi
        moon_altitude = 45 * math.sin(hour_angle - math.pi / 2)
        
        return {
            "phase": moon_phase,
            "altitude": moon_altitude,
        }

    async def _get_sun_data(self):
        """Get sun position from HA."""
        sun_entity = self.hass.states.get("sun.sun")
        
        if sun_entity:
            elevation = sun_entity.attributes.get("elevation", 0)
            return {"elevation": elevation}
        
        return {"elevation": 0}

    def _calculate_tide_state(self, moon_data, sun_data, now):
        """Determine if tide is rising, falling, or slack."""
        moon_alt = moon_data["altitude"]
        
        # Simplified: moon overhead/underfoot = high tide
        # Moon on horizon = low tide
        if abs(moon_alt) > 70:
            return "slack_high"
        elif abs(moon_alt) < 10:
            return "slack_low"
        elif moon_alt > 0 and self._is_moon_rising(now):
            return "rising"
        else:
            return "falling"

    def _is_moon_rising(self, now):
        """Check if moon is currently rising."""
        # Simplified: check if we're in the first half of the lunar day
        hour = now.hour + now.minute / 60
        # Lunar day is ~24.8 hours, moon rises ~50 min later each day
        return 0 < hour < 12.4

    def _calculate_tide_strength(self, moon_data):
        """Calculate tide strength (0-100) based on lunar phase."""
        phase = moon_data["phase"]
        
        # Spring tides at new (0) and full (0.5) moon
        # Neap tides at quarters (0.25, 0.75)
        if phase <= 0.25:
            strength = 100 * (1 - abs(phase - 0.0) / 0.25)
        elif phase <= 0.5:
            strength = 100 * (1 - abs(phase - 0.5) / 0.25)
        elif phase <= 0.75:
            strength = 100 * (1 - abs(phase - 0.5) / 0.25)
        else:
            strength = 100 * (1 - abs(phase - 1.0) / 0.25)
        
        return round(strength)

    def _predict_tide_changes(self, moon_data, now):
        """Predict next high and low tide times."""
        # Simplified: assume semi-diurnal tides (2 highs, 2 lows per ~25 hours)
        # In reality, would use harmonic analysis
        
        current_hour = now.hour + now.minute / 60
        
        # Approximate: high tides when moon is overhead/underfoot
        # Low tides when moon is on horizon
        tide_cycle = 12.42  # hours (half lunar day)
        
        # Find next high (moon overhead or underfoot)
        next_high_hours = tide_cycle - (current_hour % tide_cycle)
        if next_high_hours < 0.5:
            next_high_hours += tide_cycle
        
        # Find next low (6.21 hours after high)
        next_low_hours = next_high_hours + tide_cycle / 2
        if next_low_hours > tide_cycle:
            next_low_hours -= tide_cycle
        
        return {
            "next_high": now + timedelta(hours=next_high_hours),
            "next_low": now + timedelta(hours=next_low_hours),
        }
