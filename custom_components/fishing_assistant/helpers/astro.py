from datetime import datetime, timedelta, timezone
from typing import Dict
from skyfield.api import load, wgs84
from skyfield import almanac
import os
from homeassistant.core import HomeAssistant
import logging

_LOGGER = logging.getLogger(__name__)


async def calculate_astronomy_forecast(
    hass: HomeAssistant, lat: float, lon: float, days: int = 7
) -> Dict[str, dict]:
    """
    Calculate a simple per-day astronomy forecast.

    Returns a dict keyed by ISO date string (YYYY-MM-DD) with keys:
      - moon_phase: float (0.0..1.0), where 0 = new, 0.5 = full
      - moonrise: ISO datetime string or None
      - moonset: ISO datetime string or None
      - moon_transit: ISO datetime string or None
      - moon_underfoot: ISO datetime string or None
      - sunrise: ISO datetime string or None
      - sunset: ISO datetime string or None

    Notes:
      - Downloads a small ephemeris file if missing (uses async_add_executor_job so it won't block).
      - Uses conservative error handling and logs problems instead of raising.
    """
    ts = load.timescale()

    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)

    eph_path = os.path.join(data_dir, "de421.bsp")

    try:
        if not os.path.exists(eph_path):
            _LOGGER.info("Skyfield ephemeris not found — downloading to %s", eph_path)

            def download_eph():
                import urllib.request

                url = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de421.bsp"
                urllib.request.urlretrieve(url, eph_path)
                return load(eph_path)

            eph = await hass.async_add_executor_job(download_eph)
        else:
            eph = await hass.async_add_executor_job(lambda: load(eph_path))
    except Exception as exc:
        _LOGGER.error("Failed to load or download ephemeris: %s", exc, exc_info=True)
        # Return empty forecast (all None) for requested days
        forecast = {}
        start_date = datetime.now(timezone.utc).date()
        for i in range(days):
            d = start_date + timedelta(days=i)
            forecast[d.isoformat()] = {
                "moon_phase": None,
                "moonrise": None,
                "moonset": None,
                "moon_transit": None,
                "moon_underfoot": None,
                "sunrise": None,
                "sunset": None,
            }
        return forecast

    try:
        location = wgs84.latlon(lat, lon)
    except Exception as exc:
        _LOGGER.error("Invalid lat/lon (%s, %s): %s", lat, lon, exc, exc_info=True)
        location = wgs84.latlon(lat, lon)  # still try; let skyfield error if bad

    start_date = datetime.now(timezone.utc).date()
    end_date = start_date + timedelta(days=days)

    t0 = ts.utc(start_date.year, start_date.month, start_date.day)
    t1 = ts.utc(end_date.year, end_date.month, end_date.day)

    # Prepare containers
    events = {
        "moon_phase": {},
        "moonrise": {},
        "moonset": {},
        "moon_transit": {},
        "moon_underfoot": {},
        "sunrise": {},
        "sunset": {},
    }

    # Map skyfield moon phase integer to fractional phase [0.0..1.0]
    phase_map = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.75}

    # Helper to safely call almanac.find_discrete and log errors
    def _safe_find_discrete(t0_, t1_, func, name):
        try:
            return almanac.find_discrete(t0_, t1_, func)
        except Exception as exc:
            _LOGGER.warning("almanac.find_discrete('%s') failed: %s", name, exc)
            return [], []

    # Moon phases
    times, phases = _safe_find_discrete(t0, t1, almanac.moon_phases(eph), "moon_phases")
    for t, p in zip(times, phases):
        try:
            date_str = t.utc_datetime().date().isoformat()
            # p is an int 0..3 in skyfield - map to fraction
            frac = phase_map.get(int(p), None)
            # store as float or None
            events["moon_phase"][date_str] = float(frac) if frac is not None else None
        except Exception:
            _LOGGER.debug("Skipping moon phase event at %s (unparseable)", t, exc_info=True)

    # Moon rise / set
    times, events_raw = _safe_find_discrete(
        t0, t1, almanac.risings_and_settings(eph, eph["Moon"], location), "moon_rise_set"
    )
    for t, ev in zip(times, events_raw):
        try:
            date_str = t.utc_datetime().date().isoformat()
            key = "moonrise" if ev == 1 else "moonset"
            events[key][date_str] = t.utc_datetime().isoformat()
        except Exception:
            _LOGGER.debug("Skipping moon rise/set at %s", t, exc_info=True)

    # Moon transit / underfoot
    times, events_raw = _safe_find_discrete(
        t0, t1, almanac.meridian_transits(eph, eph["Moon"], location), "moon_transits"
    )
    for t, ev in zip(times, events_raw):
        try:
            date_str = t.utc_datetime().date().isoformat()
            key = "moon_transit" if ev == 1 else "moon_underfoot"
            events[key][date_str] = t.utc_datetime().isoformat()
        except Exception:
            _LOGGER.debug("Skipping moon transit/underfoot at %s", t, exc_info=True)

    # Sunrise / sunset
    times, events_raw = _safe_find_discrete(t0, t1, almanac.sunrise_sunset(eph, location), "sun_rise_set")
    for t, ev in zip(times, events_raw):
        try:
            date_str = t.utc_datetime().date().isoformat()
            key = "sunrise" if ev == 1 else "sunset"
            events[key][date_str] = t.utc_datetime().isoformat()
        except Exception:
            _LOGGER.debug("Skipping sunrise/sunset at %s", t, exc_info=True)

    # Build final forecast with consistent keys and ISO date keys
    forecast = {}
    for i in range(days):
        d = start_date + timedelta(days=i)
        ds = d.isoformat()
        forecast[ds] = {
            "moon_phase": events["moon_phase"].get(ds),
            "moonrise": events["moonrise"].get(ds),
            "moonset": events["moonset"].get(ds),
            "moon_transit": events["moon_transit"].get(ds),
            "moon_underfoot": events["moon_underfoot"].get(ds),
            "sunrise": events["sunrise"].get(ds),
            "sunset": events["sunset"].get(ds),
        }

    return forecast