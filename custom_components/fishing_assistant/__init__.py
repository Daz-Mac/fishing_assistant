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
    """Register the custom Lovelace card."""
    # Only register once
    if "fishing_assistant_card_registered" in hass.data.get(DOMAIN, {}):
        return
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["fishing_assistant_card_registered"] = True
    
    # Register the card resource
    card_path = Path(__file__).parent / "www" / "fishing-assistant-card.js"
    card_url = f"/fishing_assistant_local/fishing-assistant-card.js"
    
    # Register the static path
    hass.http.register_static_path(
        "/fishing_assistant_local",
        str(Path(__file__).parent / "www"),
        cache_headers=False
    )
    
    _LOGGER.info("Registered Fishing Assistant card at %s", card_url)
