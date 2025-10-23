from homeassistant.core import HomeAssistant
import datetime
from typing import Dict, Optional
import aiohttp
import pandas as pd
import logging

from .species_loader import SpeciesLoader
from .helpers.astro import calculate_astronomy_forecast

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_LOGGER = logging.getLogger(__name__)


def scale_score(score):
    stretched = (score - 0.5) / (0.9 - 0.5) * 10
    return max(0, min(10, round(stretched)))


def get_profile_weights(body_type: str) -> dict:
    if body_type not in ["lake", "river", "pond", "reservoir"]:
        _LOGGER.warning(f"Unknown body_type '{body_type}', defaulting to 'lake'.")
        body_type = "lake"

    weights = {
        "temp": 0.25,
        "cloud": 0.1,
        "pressure": 0.15,
        "wind": 0.1,
        "precip": 0.1,
        "twilight": 0.15,
        "solunar": 0.1,
        "moon": 0.05,
    }

    if body_type == "river":
        weights.update({
            "pressure": 0.05,
            "solunar": 0.05,
            "precip": 0.2,
        })
    elif body_type == "pond":
        weights.update({
            "temp": 0.3,
            "precip": 0.2,
            "pressure": 0.2,
        })
    elif body_type == "reservoir":
        weights.update({
            "pressure": 0.1,
            "solunar": 0.08,
            "moon": 0.07,
        })

    return weights


async def get_fish_score_forecast(
    hass: HomeAssistant,
    fish: str,
    lat: float,
    lon: float,
    timezone: str,
    elevation: float,
    body_type: str,
    species_loader: Optional[SpeciesLoader] = None,
) -> Dict[str, Dict[str, str | float]]:
    """
    Get fishing score forecast for a species.
    
    Args:
        hass: Home Assistant instance
        fish: Species ID (e.g., "bass", "pike", "trout")
        lat: Latitude
        lon: Longitude
        timezone: Timezone string
        elevation: Elevation in meters
        body_type: Type of water body (lake, river, pond, reservoir)
        species_loader: Optional SpeciesLoader instance (will create if not provided)
    """
    # Initialize species loader if not provided
    if species_loader is None:
        species_loader = SpeciesLoader(hass)
        await species_loader.async_load_profiles()

    # Get species profile from JSON
    fish_profile = species_loader.get_species(fish)
    
    if not fish_profile:
        _LOGGER.warning(f"No species profile found for '{fish}'")
        return {}

    # Check if this is a freshwater species
    if fish_profile.get("habitat") != "freshwater":
        _LOGGER.error(f"Species '{fish}' is not a freshwater species. Use ocean scoring instead.")
        return {}

    today = datetime.date.today()
    end_date = today + datetime.timedelta(days=6)

    # Get moon + sun event timings from Skyfield
    astro_data = await calculate_astronomy_forecast(hass, lat, lon, days=7)

    if not astro_data:
        return {}

    try:
        # Log the exact values being sent to the API
        _LOGGER.debug(f"Making Open-Meteo API request with lat={lat} ({type(lat)}), lon={lon} ({type(lon)})")
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,cloudcover,pressure_msl,precipitation,windspeed_10m",
            "daily": "sunrise,sunset",
            "timezone": timezone,
            "elevation": elevation,
            "start_date": str(today),
            "end_date": str(end_date)
        }
        _LOGGER.debug(f"Full API parameters: {params}")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                OPEN_METEO_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    _LOGGER.error(f"Open-Meteo API error: Status {response.status}, Response: {error_text}")
                    return {}

                data = await response.json()
                _LOGGER.debug(f"Open-Meteo response: {data}")
                if "hourly" not in data or "daily" not in data:
                    _LOGGER.warning(f"Fishing forecast fetch failed for {fish} at {lat}, {lon}: {data}")
                    return {}
    except Exception as e:
        _LOGGER.error(f"Exception while fetching Open-Meteo data: {e}")
        return {}

    # Units: temp °C, cloud %, pressure hPa, wind km/h, precip mm
    hourly = pd.DataFrame({
        "datetime": pd.to_datetime(data["hourly"]["time"]),
        "temp": data["hourly"]["temperature_2m"],
        "cloud": data["hourly"]["cloudcover"],
        "pressure": data["hourly"]["pressure_msl"],
        "precip": data["hourly"]["precipitation"],
        "wind": data["hourly"]["windspeed_10m"]
    })

    hourly["date"] = hourly["datetime"].dt.date
    hourly["hour"] = hourly["datetime"].dt.hour
    hourly["pressure_trend"] = hourly["pressure"].diff()

    forecast = {}
    weights = get_profile_weights(body_type)

    for date, group in hourly.groupby("date"):
        date_str = str(date)
        scores = []
        astro = astro_data.get(date_str, {})

        for _, row in group.iterrows():
            score = _score_hour(row=row, profile=fish_profile, astro=astro, weights=weights)
            scores.append((row["hour"], score))

        # Best 3-hour rolling window
        best_avg = 0
        best_window = ("--:--", "--:--")
        for i in range(len(scores) - 2):
            avg = (scores[i][1] + scores[i+1][1] + scores[i+2][1]) / 3
            if avg > best_avg:
                best_avg = avg
                best_window = (f"{scores[i][0]:02}:00", f"{scores[i+2][0]:02}:00")

        forecast[date_str] = {
            "score": scale_score(best_avg),
            "best_window": f"{best_window[0]} – {best_window[1]}"
        }
        
    _LOGGER.debug(f"Forecast for {fish} on {lat},{lon}: {forecast}")

    return forecast


def _score_hour(row, profile, astro, weights: dict) -> float:
    hour = row["hour"]

    # Extract temp_range from profile (handle both tuple and list formats)
    temp_range = profile.get("temp_range", [10, 25])
    if isinstance(temp_range, tuple):
        temp_range = list(temp_range)

    temp_score = _score_temp(row["temp"], temp_range)
    
    # Get ideal_cloud from profile
    ideal_cloud = profile.get("ideal_cloud", 50)
    cloud_score = 1 - abs(row["cloud"] - ideal_cloud) / 100
    
    press_score = _score_pressure_trend(row["pressure_trend"], profile.get("prefers_low_pressure", True))
    wind_score = _score_wind(row["wind"])
    precip_score = _score_precip(row["precip"])

    # Astro events
    sunrise = _parse_time(astro.get("sunrise"))
    sunset = _parse_time(astro.get("sunset"))
    moon_phase = astro.get("moon_phase", 0.5)
    transit = _parse_time(astro.get("moon_transit", None))
    underfoot = _parse_time(astro.get("moon_underfoot"))
    moonrise = _parse_time(astro.get("moonrise"))
    moonset = _parse_time(astro.get("moonset"))

    twilight_score = _score_twilight(hour, sunrise, sunset)
    moon_score = _score_moon_phase(moon_phase)
    solunar_score = _score_solunar(hour, transit, underfoot, moonrise, moonset)

    return round((
        temp_score * weights["temp"] +
        cloud_score * weights["cloud"] +
        press_score * weights["pressure"] +
        wind_score * weights["wind"] +
        precip_score * weights["precip"] +
        twilight_score * weights["twilight"] +
        solunar_score * weights["solunar"] +
        moon_score * weights["moon"]
    ), 2)


# ----------------------------
# Individual scoring functions
# ----------------------------

def _score_temp(temp: float, ideal_range: list) -> float:
    """Score temperature based on ideal range."""
    # Note: temp is air temp in °C, used as proxy for water temp.
    low, high = ideal_range[0], ideal_range[1]
    if temp < low:
        return max(0, (temp - (low - 10)) / 10)
    elif temp > high:
        return max(0, (high + 10 - temp) / 10)
    return 1.0


def _score_pressure_trend(trend: float, prefers_low: bool = True) -> float:
    """Score pressure trend based on species preference."""
    if pd.isna(trend):
        return 0.7
    
    if prefers_low:
        # Species that prefer falling/low pressure (most fish)
        if trend < -2:
            return 1.0
        elif trend > 2:
            return 0.4
        return 0.7
    else:
        # Species that prefer stable/rising pressure
        if trend > 2:
            return 0.9
        elif trend < -2:
            return 0.5
        return 0.8


def _score_wind(speed: float) -> float:
    # Assumes km/h
    if speed < 2:
        return 0.8
    elif speed < 6:
        return 1.0
    elif speed < 10:
        return 0.6
    return 0.2


def _score_precip(amount: float) -> float:
    # Assumes mm/h
    if amount == 0:
        return 0.7
    elif amount < 1:
        return 1.0
    elif amount < 5:
        return 0.5
    return 0.2


def _score_twilight(hour: int, sunrise, sunset) -> float:
    if not sunrise or not sunset:
        return 0.7
    if abs(hour - sunrise.hour) <= 1 or abs(hour - sunset.hour) <= 1:
        return 1.0
    return 0.7


def _score_moon_phase(phase: float) -> float:
    # 0.0 = New, 0.5 = Full, 1.0 = New
    if phase is None:
        return 0.7  # Default score when moon phase data is missing
    if phase < 0.1 or phase > 0.9:
        return 1.0
    return 0.7


def _score_solunar(hour: int, transit, underfoot, moonrise, moonset) -> float:
    boost = 0
    for event in [transit, underfoot]:
        if event and abs(hour - event.hour) <= 1:
            boost += 0.5
    for event in [moonrise, moonset]:
        if event and abs(hour - event.hour) <= 1:
            boost += 0.25
    return min(1.0, 0.6 + boost)


def _parse_time(time_str: str):
    if not time_str:
        return None
    try:
        return datetime.datetime.strptime(time_str, "%H:%M").time()
    except Exception:
        return None
    