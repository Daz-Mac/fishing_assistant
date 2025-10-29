from datetime import datetime, timedelta, timezone
from typing import Dict
from skyfield.api import load, wgs84
from skyfield import almanac
import os
from homeassistant.core import HomeAssistant
import logging
import math

# zoneinfo is available on Python 3.9+. Use it when available.
try:
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:
    ZoneInfo = None  # fallback if not available

_LOGGER = logging.getLogger(__name__)


async def calculate_astronomy_forecast(
    hass: HomeAssistant, lat: float, lon: float, days: int = 7
) -> Dict[str, dict]:
    """
    Calculate a per-day astronomy forecast.

    Returns a dict keyed by ISO date string (YYYY-MM-DD) with keys:
      - moon_phase: float (0.0..1.0), where 0 = new, 0.5 = full
      - moonrise: ISO datetime string or None
      - moonset: ISO datetime string or None
      - moon_transit: ISO datetime string or None
      - moon_underfoot: ISO datetime string or None
      - sunrise: ISO datetime string or None
      - sunset: ISO datetime string or None

    Sampling for moon phase is done at local solar noon when possible:
      1) Prefer the true sun transit time computed by skyfield/almanac.
      2) Fallback to an estimated solar noon using longitude (12:00 UTC - lon/15h).
      3) Fallback to local civil noon (12:00 local time) if HA timezone available.
      4) Final fallback is 12:00 UTC.

    The function is defensive: it logs issues and returns None for values that
    cannot be computed rather than raising.
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
        location = wgs84.latlon(lat, lon)  # still try; skyfield will raise if truly invalid

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

    # Helper to safely call almanac.find_discrete and log errors
    def _safe_find_discrete(t0_, t1_, func, name):
        try:
            return almanac.find_discrete(t0_, t1_, func)
        except Exception as exc:
            _LOGGER.warning("almanac.find_discrete('%s') failed: %s", name, exc)
            return [], []

    # -- Precompute sun transits (upper meridian) so we can sample at real solar noon --
    sun_transit_map = {}
    try:
        times_sun, events_sun = _safe_find_discrete(
            t0, t1, almanac.meridian_transits(eph, eph["Sun"], location), "sun_transits"
        )
        for t, ev in zip(times_sun, events_sun):
            try:
                # ev == 1 indicates upper transit (solar noon); ev == 0 indicates lower transit
                if int(ev) != 1:
                    continue
                date_str = t.utc_datetime().date().isoformat()
                sun_transit_map[date_str] = t.utc_datetime()  # tz-aware naive in UTC
            except Exception:
                _LOGGER.debug("Skipping sun transit at %s", t, exc_info=True)
    except Exception:
        # Already logged in _safe_find_discrete; continue with empty map
        sun_transit_map = {}

    # Determine timezone to sample local civil noon if needed
    tz = None
    try:
        tz_name = None
        if hasattr(hass, "config") and getattr(hass.config, "time_zone", None):
            tz_name = hass.config.time_zone
        elif hasattr(hass, "timezone") and hass.timezone:
            tz_name = str(hass.timezone)
        if tz_name and ZoneInfo is not None:
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                _LOGGER.debug("ZoneInfo could not load %s, falling back to UTC", tz_name, exc_info=True)
                tz = timezone.utc
        else:
            tz = timezone.utc
    except Exception:
        tz = timezone.utc

    # Helper to estimate solar noon using longitude
    def _estimated_solar_noon_utc(d: datetime.date) -> datetime:
        # Solar noon UTC ~= 12:00 UTC - (lon / 15 hours)
        try:
            offset_hours = lon / 15.0
            # Build a timezone-aware datetime at 12:00 UTC and apply offset
            dt_utc_noon = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
            est = dt_utc_noon - timedelta(hours=offset_hours)
            return est
        except Exception:
            return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)

    # --- Compute a per-day continuous moon_phase fraction (0.0..1.0) at local solar noon when possible ---
    try:
        earth = eph["earth"]
        sun = eph["sun"]
        moon = eph["moon"]
        for i in range(days):
            d = start_date + timedelta(days=i)
            ds = d.isoformat()

            # Decide sampling time (prefer true sun transit)
            used_sampling = "none"
            t_sample_dt = None  # a timezone-aware datetime in UTC

            # 1) Use the precise sun transit time if we have it
            if ds in sun_transit_map:
                t_sample_dt = sun_transit_map[ds]  # already utc datetime
                used_sampling = "sun_transit"
            else:
                # 2) Attempt estimated solar noon from longitude
                try:
                    t_sample_dt = _estimated_solar_noon_utc(d)
                    used_sampling = "estimated_solar_noon_from_longitude"
                except Exception:
                    t_sample_dt = None

            # 3) If still None or invalid, try local civil noon (12:00 local)
            if t_sample_dt is None or not isinstance(t_sample_dt, datetime):
                try:
                    if tz is not None and ZoneInfo is not None:
                        local_noon = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=tz)
                        t_sample_dt = local_noon.astimezone(timezone.utc)
                        used_sampling = "local_civil_noon"
                    else:
                        # fallback to 12:00 UTC
                        t_sample_dt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                        used_sampling = "utc_noon_fallback"
                except Exception:
                    t_sample_dt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                    used_sampling = "utc_noon_fallback"

            # Build skyfield Time from the UTC datetime
            try:
                t_sample = ts.utc(
                    t_sample_dt.year,
                    t_sample_dt.month,
                    t_sample_dt.day,
                    t_sample_dt.hour,
                    t_sample_dt.minute,
                    t_sample_dt.second,
                )
            except Exception:
                # final fallback to simple midday UTC
                t_sample = ts.utc(d.year, d.month, d.day, 12, 0, 0)
                used_sampling = "final_utc_noon"

            try:
                # Compute Sun-Moon angular separation at t_sample and derive illuminated fraction
                astrom_sun = earth.at(t_sample).observe(sun).apparent()
                astrom_moon = earth.at(t_sample).observe(moon).apparent()
                sep = astrom_sun.separation_from(astrom_moon).radians
                frac = (1.0 + math.cos(sep)) / 2.0
                frac = max(0.0, min(1.0, float(frac)))
                events["moon_phase"][ds] = frac
            except Exception:
                _LOGGER.debug(
                    "Failed to compute moon phase for %s using sampling=%s", ds, used_sampling, exc_info=True
                )
                events["moon_phase"][ds] = None
    except Exception:
        _LOGGER.warning("Per-day moon phase sampling failed; falling back to discrete phase events")
        phase_map = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.75}
        times, phases = _safe_find_discrete(t0, t1, almanac.moon_phases(eph), "moon_phases")
        for t, p in zip(times, phases):
            try:
                date_str = t.utc_datetime().date().isoformat()
                frac = phase_map.get(int(p), None)
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

    # --- Fallbacks for missing moon events: try a larger search window (day-1..day+2) ---
    def _attempt_fallback_event_lookup(func_factory, eph_obj, loc, target_date, name):
        d0 = target_date - timedelta(days=1)
        d1 = target_date + timedelta(days=2)
        t0_fb = ts.utc(d0.year, d0.month, d0.day)
        t1_fb = ts.utc(d1.year, d1.month, d1.day)
        try:
            times_fb, evs_fb = almanac.find_discrete(t0_fb, t1_fb, func_factory(eph_obj, loc))
            return times_fb, evs_fb
        except Exception as exc:
            _LOGGER.debug("Fallback almanac.find_discrete('%s') failed: %s", name, exc)
            return [], []

    # For each day, if moonrise/moonset/transit missing, try fallback and assign nearest event
    for i in range(days):
        d = start_date + timedelta(days=i)
        ds = d.isoformat()

        # moonrise / moonset
        if ds not in events["moonrise"] and ds not in events["moonset"]:
            times_fb, evs_fb = _attempt_fallback_event_lookup(
                lambda e, l: almanac.risings_and_settings(e, e["Moon"], l),
                eph,
                location,
                d,
                "moon_rise_set_fallback",
            )
            for t, ev in zip(times_fb, evs_fb):
                try:
                    date_str = t.utc_datetime().date().isoformat()
                    key = "moonrise" if ev == 1 else "moonset"
                    events[key].setdefault(date_str, t.utc_datetime().isoformat())
                except Exception:
                    _LOGGER.debug("Skipping fallback moon rise/set at %s", t, exc_info=True)

        # moon transit / underfoot
        if ds not in events["moon_transit"] and ds not in events["moon_underfoot"]:
            times_fb, evs_fb = _attempt_fallback_event_lookup(
                lambda e, l: almanac.meridian_transits(e, e["Moon"], l),
                eph,
                location,
                d,
                "moon_transit_fallback",
            )
            for t, ev in zip(times_fb, evs_fb):
                try:
                    date_str = t.utc_datetime().date().isoformat()
                    key = "moon_transit" if ev == 1 else "moon_underfoot"
                    events[key].setdefault(date_str, t.utc_datetime().isoformat())
                except Exception:
                    _LOGGER.debug("Skipping fallback moon transit at %s", t, exc_info=True)

    # Build final forecast dict with consistent keys and ISO date keys
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