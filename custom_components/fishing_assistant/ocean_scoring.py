"""Ocean fishing scoring algorithm."""
import logging
from datetime import datetime
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HABITAT_PRESET,
    CONF_SPECIES_FOCUS,
    CONF_THRESHOLDS,
    HABITAT_PRESETS,
    SPECIES_FOCUS,
    TIDE_STATE_RISING,
    TIDE_STATE_FALLING,
    TIDE_STATE_SLACK_HIGH,
    TIDE_STATE_SLACK_LOW,
    LIGHT_DAWN,
    LIGHT_DAY,
    LIGHT_DUSK,
    LIGHT_NIGHT,
)

_LOGGER = logging.getLogger(__name__)


class OceanFishingScorer:
    """Calculate fishing scores for ocean/shore fishing."""

    def __init__(self, hass, config):
        """Initialize the scorer."""
        self.hass = hass
        self.config = config
        self.habitat = HABITAT_PRESETS[config.get(CONF_HABITAT_PRESET)]
        self.species = SPECIES_FOCUS[config.get(CONF_SPECIES_FOCUS)]
        self.thresholds = config.get(CONF_THRESHOLDS, {})

    def calculate_score(self, weather_data, tide_data, marine_data, astro_data):
        """
        Calculate overall fishing score (0-10).
        
        Args:
            weather_data: Dict with temp, wind, clouds, rain, pressure
            tide_data: Dict with state, strength, next_high, next_low
            marine_data: Dict with wave_height, wave_period
            astro_data: Dict with sunrise, sunset, moon_phase, etc.
        
        Returns:
            Dict with score, breakdown, and best_window
        """
        now = dt_util.now()
        
        # Safety check first
        if not self._is_safe(weather_data, marine_data):
            return {
                "score": 0,
                "safety": "unsafe",
                "reason": "Conditions exceed safety thresholds",
                "breakdown": {},
            }

        # Calculate component scores
        scores = {
            "tide": self._score_tide(tide_data),
            "weather": self._score_weather(weather_data),
            "waves": self._score_waves(marine_data),
            "light": self._score_light(astro_data, now),
            "moon": self._score_moon(astro_data),
            "season": self._score_season(now),
            "pressure": self._score_pressure(weather_data),
        }

        # Apply habitat weights
        weighted_score = (
            scores["tide"] * self.habitat.get("tide_weight", 0.3) +
            scores["weather"] * self.habitat.get("wind_weight", 0.2) +
            scores["waves"] * self.habitat.get("wave_weight", 0.2) +
            scores["light"] * 0.15 +
            scores["moon"] * 0.1 +
            scores["season"] * 0.03 +
            scores["pressure"] * 0.02
        )

        # Apply species-specific bonuses
        final_score = self._apply_species_bonuses(weighted_score, scores, marine_data)

        # Determine best fishing window
        best_window = self._calculate_best_window(tide_data, astro_data, now)

        return {
            "score": round(min(10, max(0, final_score)), 1),
            "safety": "safe",
            "breakdown": scores,
            "best_window": best_window,
            "tide_state": tide_data.get("state"),
            "conditions_summary": self._generate_summary(scores, tide_data, marine_data),
        }

    def _is_safe(self, weather_data, marine_data):
        """Check if conditions are safe for fishing."""
        # Wind check
        wind_speed = weather_data.get("wind_speed", 0)
        if wind_speed > self.thresholds.get("max_wind_speed", 25):
            return False

        # Gust check
        wind_gust = weather_data.get("wind_gust", wind_speed)
        if wind_gust > self.thresholds.get("max_gust_speed", 40):
            return False

        # Wave check
        wave_height = marine_data.get("current", {}).get("wave_height")
        if wave_height and wave_height > self.thresholds.get("max_wave_height", 2.0):
            return False

        return True

    def _score_tide(self, tide_data):
        """Score tide conditions (0-100)."""
        state = tide_data.get("state", "unknown")
        strength = tide_data.get("strength", 50)
        
        # Species preference for tide state
        preferred_tide = self.species.get("best_tide", "moving")
        
        base_score = 50
        
        if preferred_tide == "moving":
            if state in [TIDE_STATE_RISING, TIDE_STATE_FALLING]:
                base_score = 90
            else:
                base_score = 40
        elif preferred_tide == "rising":
            if state == TIDE_STATE_RISING:
                base_score = 100
            elif state == TIDE_STATE_FALLING:
                base_score = 60
            else:
                base_score = 40
        elif preferred_tide == "slack_high":
            if state == TIDE_STATE_SLACK_HIGH:
                base_score = 100
            elif state == TIDE_STATE_SLACK_LOW:
                base_score = 30
            else:
                base_score = 70
        elif preferred_tide == "slack":
            if state in [TIDE_STATE_SLACK_HIGH, TIDE_STATE_SLACK_LOW]:
                base_score = 100
            else:
                base_score = 50
        else:  # "any"
            base_score = 80

        # Adjust for tide strength (spring vs neap)
        # Spring tides (high strength) are generally better
        strength_bonus = (strength - 50) / 5  # -10 to +10
        
        return min(100, max(0, base_score + strength_bonus))

    def _score_weather(self, weather_data):
        """Score weather conditions (0-100)."""
        score = 100
        
        # Wind penalty
        wind_speed = weather_data.get("wind_speed", 0)
        max_wind = self.habitat.get("max_wind_speed", 25)
        if wind_speed > max_wind * 0.7:
            penalty = ((wind_speed - max_wind * 0.7) / (max_wind * 0.3)) * 30
            score -= penalty

        # Rain penalty
        rain_chance = weather_data.get("precipitation_probability", 0)
        if rain_chance > 30:
            score -= (rain_chance - 30) * 0.5

        # Cloud bonus (species-specific)
        cloud_cover = weather_data.get("cloud_cover", 50)
        cloud_bonus = self.species.get("cloud_bonus", 0.5)
        if 40 <= cloud_cover <= 80:
            score += 10 * cloud_bonus

        # Temperature (water temp proxy)
        temp = weather_data.get("temperature", 15)
        if 12 <= temp <= 22:
            score += 10
        elif temp < 8 or temp > 28:
            score -= 20

        return min(100, max(0, score))

    def _score_waves(self, marine_data):
        """Score wave conditions (0-100)."""
        wave_height = marine_data.get("current", {}).get("wave_height")
        
        if wave_height is None:
            return 70  # Neutral when no data

        wave_pref = self.species.get("wave_preference", "moderate")
        
        if wave_pref == "calm":
            if wave_height < 0.5:
                return 100
            elif wave_height < 1.0:
                return 70
            else:
                return 40
        elif wave_pref == "active":
            if 0.8 <= wave_height <= 1.8:
                return 100
            elif wave_height < 0.5:
                return 50
            else:
                return 60
        else:  # moderate
            if 0.5 <= wave_height <= 1.2:
                return 100
            elif wave_height < 0.3:
                return 60
            elif wave_height < 2.0:
                return 70
            else:
                return 40

    def _score_light(self, astro_data, now):
        """Score light conditions based on time of day (0-100)."""
        light_condition = self._get_light_condition(astro_data, now)
        light_pref = self.species.get("light_preference", "dawn_dusk")
        
        scores = {
            "dawn": {LIGHT_DAWN: 100, LIGHT_DAY: 70, LIGHT_DUSK: 90, LIGHT_NIGHT: 40},
            "day": {LIGHT_DAWN: 80, LIGHT_DAY: 100, LIGHT_DUSK: 80, LIGHT_NIGHT: 30},
            "dusk": {LIGHT_DAWN: 90, LIGHT_DAY: 70, LIGHT_DUSK: 100, LIGHT_NIGHT: 40},
            "night": {LIGHT_DAWN: 60, LIGHT_DAY: 20, LIGHT_DUSK: 60, LIGHT_NIGHT: 100},
            "dawn_dusk": {LIGHT_DAWN: 100, LIGHT_DAY: 60, LIGHT_DUSK: 100, LIGHT_NIGHT: 50},
            "low_light": {LIGHT_DAWN: 100, LIGHT_DAY: 50, LIGHT_DUSK: 100, LIGHT_NIGHT: 90},
        }
        
        return scores.get(light_pref, {}).get(light_condition, 70)

    def _get_light_condition(self, astro_data, now):
        """Determine current light condition."""
        sunrise = astro_data.get("sunrise")
        sunset = astro_data.get("sunset")
        
        if not sunrise or not sunset:
            return LIGHT_DAY

        # Dawn: 1 hour before to 1 hour after sunrise
        if sunrise - timedelta(hours=1) <= now <= sunrise + timedelta(hours=1):
            return LIGHT_DAWN
        # Dusk: 1 hour before to 1 hour after sunset
        elif sunset - timedelta(hours=1) <= now <= sunset + timedelta(hours=1):
            return LIGHT_DUSK
        # Day: between dawn and dusk
        elif sunrise + timedelta(hours=1) < now < sunset - timedelta(hours=1):
            return LIGHT_DAY
        # Night: everything else
        else:
            return LIGHT_NIGHT

    def _score_moon(self, astro_data):
        """Score moon phase (0-100)."""
        moon_phase = astro_data.get("moon_phase", 0.5)
        
        # New moon (0) and full moon (0.5) are best
        # Quarters (0.25, 0.75) are worst
        if moon_phase <= 0.25:
            distance_from_new = abs(moon_phase - 0.0)
            return 100 - (distance_from_new / 0.25) * 30
        elif moon_phase <= 0.5:
            distance_from_full = abs(moon_phase - 0.5)
            return 100 - (distance_from_full / 0.25) * 30
        elif moon_phase <= 0.75:
            distance_from_full = abs(moon_phase - 0.5)
            return 100 - (distance_from_full / 0.25) * 30
        else:
            distance_from_new = abs(moon_phase - 1.0)
            return 100 - (distance_from_new / 0.25) * 30

    def _score_season(self, now):
        """Score based on species active months (0-100)."""
        current_month = now.month
        active_months = self.species.get("active_months", list(range(1, 13)))
        
        if current_month in active_months:
            return 100
        else:
            return 30

    def _score_pressure(self, weather_data):
        """Score barometric pressure trend (0-100)."""
        pressure = weather_data.get("pressure", 1013)
        
        # Stable or rising pressure is good
        # Falling pressure can be good before a storm
        if 1010 <= pressure <= 1020:
            return 100
        elif 1005 <= pressure < 1010:
            return 80
        elif 1020 < pressure <= 1025:
            return 90
        else:
            return 60

    def _apply_species_bonuses(self, base_score, scores, marine_data):
        """Apply species-specific bonuses."""
        final_score = base_score
        
        # Wave bonus for surf predators
        if self.species.get("wave_bonus") and scores["waves"] > 80:
            final_score += 1.0

        return final_score

    def _calculate_best_window(self, tide_data, astro_data, now):
        """Calculate best fishing window for today."""
        # Simplified: return next tide change + dawn/dusk
        windows = []
        
        # Add tide windows
        next_high = tide_data.get("next_high")
        next_low = tide_data.get("next_low")
        
        if next_high and next_high.date() == now.date():
            windows.append(("Tide High", next_high))
        if next_low and next_low.date() == now.date():
            windows.append(("Tide Low", next_low))
        
        # Add dawn/dusk
        sunrise = astro_data.get("sunrise")
        sunset = astro_data.get("sunset")
        
        if sunrise:
            windows.append(("Dawn", sunrise))
        if sunset:
            windows.append(("Dusk", sunset))
        
        # Return earliest upcoming window
        future_windows = [(name, time) for name, time in windows if time > now]
        if future_windows:
            future_windows.sort(key=lambda x: x[1])
            name, time = future_windows[0]
            return f"{name} at {time.strftime('%H:%M')}"
        
        return "Check tomorrow"

    def _generate_summary(self, scores, tide_data, marine_data):
        """Generate human-readable conditions summary."""
        parts = []
        
        # Tide
        tide_state = tide_data.get("state", "unknown")
        parts.append(f"Tide: {tide_state.replace('_', ' ').title()}")
        
        # Waves
        wave_height = marine_data.get("current", {}).get("wave_height")
        if wave_height:
            parts.append(f"Waves: {wave_height:.1f}m")
        
        # Overall quality
        avg_score = sum(scores.values()) / len(scores)
        if avg_score >= 80:
            parts.append("Excellent conditions")
        elif avg_score >= 60:
            parts.append("Good conditions")
        else:
            parts.append("Fair conditions")
        
        return " | ".join(parts)
