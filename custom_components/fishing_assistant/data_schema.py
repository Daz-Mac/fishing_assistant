"""Data structure definitions for Fishing Assistant integration.

This module defines TypedDict classes for consistent data structures
used throughout the integration between freshwater and ocean modes,
and between backend sensors and frontend card rendering.
"""

from typing import TypedDict, Optional, Dict, List


class WeatherData(TypedDict, total=False):
    """Standard weather data structure."""
    temperature: float  # Celsius
    wind_speed: float  # km/h
    wind_gust: float  # km/h
    cloud_cover: float  # percentage (0-100)
    precipitation_probability: float  # percentage (0-100)
    pressure: float  # hPa
    datetime: str  # ISO format datetime string


class MarineData(TypedDict, total=False):
    """Marine/wave data structure."""
    wave_height: Optional[float]  # meters
    wave_period: Optional[float]  # seconds
    wave_direction: Optional[float]  # degrees
    wind_wave_height: Optional[float]  # meters
    wind_wave_period: Optional[float]  # seconds
    swell_wave_height: Optional[float]  # meters
    swell_wave_period: Optional[float]  # seconds
    timestamp: str  # ISO format datetime string


class TideData(TypedDict, total=False):
    """Tide information structure."""
    state: str  # rising, falling, slack_high, slack_low
    strength: int  # 0-100 percentage
    next_high: str  # ISO format datetime string
    next_low: str  # ISO format datetime string
    confidence: str  # proxy, api, sensor
    source: str  # Description of data source


class AstroData(TypedDict, total=False):
    """Astronomical data structure."""
    moon_phase: Optional[float]  # 0-1 (0=new, 0.5=full)
    moonrise: Optional[str]  # HH:MM format
    moonset: Optional[str]  # HH:MM format
    moon_transit: Optional[str]  # HH:MM format
    moon_underfoot: Optional[str]  # HH:MM format
    sunrise: Optional[str]  # HH:MM format
    sunset: Optional[str]  # HH:MM format


class ComponentScores(TypedDict, total=False):
    """Component scores breakdown."""
    Season: float
    Temperature: float
    Wind: float
    Pressure: float
    Tide: float
    Moon: float
    Time: float
    Waves: float
    Safety: float


class ScoringResult(TypedDict, total=False):
    """Scoring result structure (0-10 scale)."""
    score: float  # 0-10
    breakdown: Dict  # Additional breakdown info
    component_scores: ComponentScores  # Individual component scores
    conditions_summary: str  # Human-readable summary


class PeriodForecast(TypedDict, total=False):
    """Forecast for a specific time period."""
    time_block: str  # morning, afternoon, evening, night, dawn, dusk
    hours: str  # HH:MM-HH:MM format
    score: float  # 0-10
    component_scores: ComponentScores
    safety: str  # safe, caution, unsafe
    safety_reasons: List[str]
    tide_state: str  # rising, falling, slack_high, slack_low, n/a
    conditions: str  # Human-readable summary
    weather: WeatherData


class DailyForecast(TypedDict, total=False):
    """Daily forecast structure."""
    date: str  # ISO date string (YYYY-MM-DD)
    day_name: str  # Monday, Tuesday, etc.
    periods: Dict[str, PeriodForecast]  # Dictionary keyed by period name
    daily_avg_score: float  # 0-10
    best_period: Optional[str]  # Name of best period
    best_score: float  # Score of best period


class SensorAttributes(TypedDict, total=False):
    """Standard sensor attributes structure."""
    score: float  # 0-10
    conditions: str
    component_scores: ComponentScores
    weather: WeatherData
    marine: Optional[MarineData]
    tide: Optional[TideData]
    astro: AstroData
    forecast: Dict[str, DailyForecast]  # Keyed by ISO date string
    mode: str  # freshwater or ocean
    species: List[str]
    location: str
    last_updated: str  # ISO format datetime string