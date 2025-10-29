import logging
from pathlib import Path
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.core import HomeAssistant
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Fishing Assistant from YAML (not used)."""
    # Register the custom card
    await _register_custom_card(hass)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fishing Assistant from a config entry."""
    _LOGGER.debug("Setting up entry: %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Register the custom card
    await _register_custom_card(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading entry: %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok

async def _register_custom_card(hass: HomeAssistant) -> None:
    """Register the custom Lovelace card.

    This registers a static path for the card under /fishing_assistant_local so users
    can add it as a JavaScript module resource in the frontend. Auto-reload of
    Lovelace resources is intentionally not forced by the integration.
    """
    # Only register once
    if "fishing_assistant_card_registered" in hass.data.get(DOMAIN, {}):
        return

    hass.data.setdefault(DOMAIN, {})

    # Register the card resource directory
    card_dir = Path(__file__).parent / "www"
    card_url = "/fishing_assistant_local"

    try:
        # Use the synchronous API available on hass.http to register a static path.
        # This avoids depending on a specific StaticPathConfig or async helper which
        # may change across HA versions.
        try:
            # Some HA versions expose register_static_path
            hass.http.register_static_path(card_url, str(card_dir), cache_headers=False)
        except Exception:
            # Fallback - some HA versions use a different method name; attempt the common one
            try:
                hass.http.register_static_path(str(card_dir), card_url, cache_headers=False)
            except Exception as exc:
                _LOGGER.debug("Could not register static path for fishing assistant card: %s", exc)
    except Exception as e:
        _LOGGER.debug("Could not register static paths for fishing assistant card: %s", e)

    # Log instructions for the user to add the resource manually to Lovelace
    resource_url = f"{card_url}/fishing-assistant-card.js"
    _LOGGER.info("Fishing Assistant card static files available at %s", resource_url)
    _LOGGER.info("Please add the card resource manually in Lovelace: Settings → Dashboards → Resources")
    _LOGGER.info("Resource URL: %s", resource_url)
    _LOGGER.info("Resource Type: JavaScript Module")

    hass.data[DOMAIN]["fishing_assistant_card_registered"] = True