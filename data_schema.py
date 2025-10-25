"""Unified data schema for Fishing Assistant integration.

This module defines the standard data structures used throughout the integration
to ensure consistency between freshwater and ocean modes, and between backend
sensors and frontend card rendering.
"""

from typing import TypedDict, List, Optional, Dict, Any
from datetime import datetime


# ============================================================================
# WEATHER DATA SCHEMA
# ============================================================================

class WeatherData(TypedDict, total=False):
    """Standard weather data structure.
    
    All weather data should use these exact key names to ensure consistency
    between weather_fetcher, scoring algorithms, and the frontend card.
    """
    temperature: Optional[float]  # Celsius
    wind_speed: float  # km/h
    wind_gust: float  # km/h
    cloud_cover: float  # Percentage (0-100)
    precipitation_probability: float  # Percentage (0-100)
    pressure: float  # hPa (hectopascals/millibars)


# ============================================================================
# MARINE DATA SCHEMA
# ============================================================================

class MarineCurrentData(TypedDict, total=False):
    """Current marine conditions."""
    wave_height: Optional[float]  # meters
    wave_period: Optional[float]  # seconds
    wave_direction: Optional[float]  # degrees
    wind_wave_height: Optional[float]  # meters
    swell_wave_height: Optional[float]  # meters


class MarineForecastDay(TypedDict, total=False):
    """Marine forecast for a single day."""
    wave_height_avg: Optional[float]  # meters
    wave_height_max: Optional[float]  # meters
    wave_period_avg: Optional[float]  # seconds


class MarineData(TypedDict, total=False):
    """Complete marine data structure."""
    current: MarineCurrentData
    forecast: Dict[str, MarineForecastDay]  # date_key -> forecast


# ============================================================================
# TIDE DATA SCHEMA
# ============================================================================

class TideData(TypedDict, total=False):
    """Tide information."""
    state: str  # "rising", "falling", "slack_high", "slack_low", "unknown"
    strength: int  # Percentage (0-100), where 100 is spring tide
    next_high: Optional[str]  # ISO datetime string
    next_low: Optional[str]  # ISO datetime string
    source: Optional[str]  # "proxy", "api", etc.


# ============================================================================
# ASTRONOMICAL DATA SCHEMA
# ============================================================================

class AstroData(TypedDict, total=False):
    """Astronomical data for fishing calculations."""
    sunrise: Optional[datetime]  # datetime object
    sunset: Optional[datetime]  # datetime object
    moon_phase: float  # 0.0 to 1.0 (0=new, 0.5=full)
    moonrise: Optional[str]  # Time string "HH:MM"
    moonset: Optional[str]  # Time string "HH:MM"
    moon_transit: Optional[str]  # Time string "HH:MM" (moon overhead)
    moon_underfoot: Optional[str]  # Time string "HH:MM" (moon opposite)
    source: Optional[str]  # "skyfield", "fallback", etc.


# ============================================================================
# SCORING RESULT SCHEMA
# ============================================================================

class ComponentScores(TypedDict, total=False):
    """Individual component scores (0-1 scale)."""
    # Ocean mode components
    tide: Optional[float]
    weather: Optional[float]
    waves: Optional[float]
    light: Optional[float]
    moon: Optional[float]
    season: Optional[float]
    pressure: Optional[float]
    
    # Freshwater mode components
    Season: Optional[float]
    Temperature: Optional[float]
    Wind: Optional[float]
    Pressure: Optional[float]
    # Note: Capitalization inconsistency to be fixed in future refactor


class ScoreBreakdown(TypedDict, total=False):
    """Detailed breakdown of score calculation."""
    component_scores: ComponentScores
    weights: Optional[Dict[str, float]]  # Component weights (ocean mode)
    species: Optional[str]  # Species name
    species_count: Optional[int]  # Number of species (freshwater)
    body_type: Optional[str]  # Water body type (freshwater)
    target_species: Optional[List[str]]  # Species names (freshwater)


class ScoringResult(TypedDict, total=False):
    """Result from scoring calculation."""
    score: float  # 0-10 scale
    safety: Optional[str]  # "safe", "caution", "unsafe" (ocean only)
    safety_reasons: Optional[List[str]]  # Safety warnings (ocean only)
    tide_state: Optional[str]  # Tide state (ocean only)
    best_window: Optional[str]  # Best fishing window description
    conditions_summary: str  # Human-readable summary
    breakdown: ScoreBreakdown


# ============================================================================
# FORECAST PERIOD SCHEMA
# ============================================================================

class ForecastPeriodWeather(TypedDict, total=False):
    """Weather data for a forecast period."""
    temperature: Optional[float]
    wind_speed: float
    wind_gust: float
    cloud_cover: float
    precipitation_probability: float
    pressure: float


class ForecastPeriodMarine(TypedDict, total=False):
    """Marine data for a forecast period (ocean only)."""
    wave_height: Optional[float]
    wave_period: Optional[float]


class ForecastPeriod(TypedDict, total=False):
    """Single time period within a day's forecast."""
    time_block: str  # "morning", "afternoon", "evening", "night", "dawn", "dusk"
    hours: str  # Time range display "HH:MM-HH:MM"
    score: float  # 0-10 scale
    safety: str  # "safe", "caution", "unsafe", "n/a"
    safety_reasons: List[str]
    tide_state: str  # Tide state or "n/a" for freshwater
    conditions: str  # Human-readable conditions summary
    weather: ForecastPeriodWeather
    marine: Optional[ForecastPeriodMarine]  # Ocean mode only
    component_scores: Optional[ComponentScores]  # Freshwater mode


class ForecastDay(TypedDict, total=False):
    """Forecast for a single day."""
    date: str  # ISO date string "YYYY-MM-DD"
    day_name: str  # "Monday", "Tuesday", etc.
    periods: Dict[str, ForecastPeriod]  # period_name -> period_data
    daily_avg_score: float  # Average score across all periods
    best_period: Optional[str]  # Name of best period
    best_score: float  # Score of best period


# ============================================================================
# SENSOR ATTRIBUTE SCHEMA
# ============================================================================

class FreshwaterSensorAttributes(TypedDict, total=False):
    """Attributes for freshwater fishing score sensor."""
    fish: str  # Species ID
    location: str  # Location name
    lat: float
    lon: float
    body_type: str  # "lake", "river", "pond"
    habitat: str  # Same as body_type
    timezone: str
    elevation: float
    period_type: str  # "full_day" or "dawn_dusk"
    weather_entity: Optional[str]
    breakdown: ScoreBreakdown
    forecast: Dict[str, ForecastDay]  # date_key -> day_forecast


class OceanSensorAttributes(TypedDict, total=False):
    """Attributes for ocean fishing score sensor."""
    location: str
    location_key: str
    latitude: float
    longitude: float
    mode: str  # "ocean"
    habitat: Optional[str]  # Habitat preset name
    species_focus: str  # Species name
    safety: str  # "safe", "caution", "unsafe"
    tide_state: str  # Current tide state
    best_window: str  # Best fishing window description
    conditions_summary: str  # Human-readable summary
    breakdown: ScoreBreakdown
    last_updated: str  # ISO datetime string
    forecast: Dict[str, ForecastDay]  # date_key -> day_forecast


# ============================================================================
# CARD DATA SCHEMA
# ============================================================================

class CardForecastPeriod(TypedDict, total=False):
    """Forecast period data as expected by the frontend card.
    
    This matches the structure that fishing-assistant-card.js expects.
    """
    time_block: str
    hours: str
    score: float
    safety: str
    tide_state: str
    conditions: str
    weather: ForecastPeriodWeather
    marine: Optional[ForecastPeriodMarine]


class CardForecastDay(TypedDict, total=False):
    """Forecast day data as expected by the frontend card."""
    date: str
    day_name: str
    periods: Dict[str, CardForecastPeriod]  # MUST be dict, not array!
    daily_avg_score: float
    best_period: Optional[str]
    best_score: float


# ============================================================================
# KEY MAPPING UTILITIES
# ============================================================================

# Standard key names for weather data
WEATHER_KEYS = {
    "temperature": "temperature",
    "wind_speed": "wind_speed",
    "wind_gust": "wind_gust",
    "cloud_cover": "cloud_cover",  # STANDARD KEY
    "cloud_coverage": "cloud_cover",  # ALIAS (to be normalized)
    "precipitation_probability": "precipitation_probability",
    "pressure": "pressure",
}


def normalize_weather_data(raw_data: Dict[str, Any]) -> WeatherData:
    """Normalize weather data to standard schema.
    
    Args:
        raw_data: Raw weather data with potentially inconsistent keys
        
    Returns:
        WeatherData with standardized keys
    """
    normalized: WeatherData = {
        "temperature": raw_data.get("temperature"),
        "wind_speed": raw_data.get("wind_speed", 0),
        "wind_gust": raw_data.get("wind_gust", raw_data.get("wind_speed", 0)),
        "cloud_cover": raw_data.get("cloud_cover", raw_data.get("cloud_coverage", 50)),
        "precipitation_probability": raw_data.get("precipitation_probability", 0),
        "pressure": raw_data.get("pressure", 1013),
    }
    return normalized


def normalize_component_scores(scores: Dict[str, float], mode: str) -> ComponentScores:
    """Normalize component scores to consistent format.
    
    Args:
        scores: Raw component scores
        mode: "freshwater" or "ocean"
        
    Returns:
        ComponentScores with standardized keys
    """
    if mode == "ocean":
        # Ocean mode already uses lowercase keys
        return scores
    else:
        # Freshwater mode uses capitalized keys - keep for now
        # TODO: Standardize to lowercase in future refactor
        return scores


# ============================================================================
# VALIDATION UTILITIES
# ============================================================================

def validate_weather_data(data: Dict[str, Any]) -> bool:
    """Validate weather data structure.
    
    Args:
        data: Weather data to validate
        
    Returns:
        True if valid, False otherwise
    """
    required_keys = ["wind_speed", "cloud_cover", "pressure"]
    return all(key in data for key in required_keys)


def validate_forecast_structure(forecast: Dict[str, Any]) -> bool:
    """Validate forecast data structure.
    
    Args:
        forecast: Forecast data to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not isinstance(forecast, dict):
        return False
    
    for date_key, day_data in forecast.items():
        if not isinstance(day_data, dict):
            return False
        
        # Check required keys
        if "periods" not in day_data:
            return False
        
        # Periods MUST be a dict, not an array
        if not isinstance(day_data["periods"], dict):
            return False
    
    return True


# ============================================================================
# CONSTANTS
# ============================================================================

# Score scale
SCORE_MIN = 0.0
SCORE_MAX = 10.0

# Safety levels
SAFETY_SAFE = "safe"
SAFETY_CAUTION = "caution"
SAFETY_UNSAFE = "unsafe"
SAFETY_NA = "n/a"

# Tide states
TIDE_RISING = "rising"
TIDE_FALLING = "falling"
TIDE_SLACK_HIGH = "slack_high"
TIDE_SLACK_LOW = "slack_low"
TIDE_UNKNOWN = "unknown"
TIDE_NA = "n/a"

# Light conditions
LIGHT_DAWN = "dawn"
LIGHT_DAY = "day"
LIGHT_DUSK = "dusk"
LIGHT_NIGHT = "night"

# Time periods
PERIOD_MORNING = "morning"
PERIOD_AFTERNOON = "afternoon"
PERIOD_EVENING = "evening"
PERIOD_NIGHT = "night"
PERIOD_DAWN = "dawn"
PERIOD_DUSK = "dusk"