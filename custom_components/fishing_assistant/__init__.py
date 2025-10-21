import logging
from pathlib import Path
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.components.http import StaticPathConfig
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
    
    # Register the card resource directory
    card_dir = Path(__file__).parent / "www"
    card_url = "/fishing_assistant_local"
    
    # Register the static path using async method with keyword arguments
    await hass.http.async_register_static_paths([
        StaticPathConfig(url_path=card_url, path=str(card_dir), cache_headers=False)
    ])
    
    # Auto-register the resource in Lovelace
    resource_url = f"{card_url}/fishing-assistant-card.js"
    
    try:
        # Try to auto-register the resource using the service call method
        await hass.services.async_call(
            "lovelace",
            "reload_resources",
            {},
            blocking=False,
        )
        
        _LOGGER.info("Registered Fishing Assistant card at %s", resource_url)
        _LOGGER.info("Please add the card resource manually in Lovelace: Settings → Dashboards → Resources")
        _LOGGER.info("Resource URL: %s", resource_url)
        _LOGGER.info("Resource Type: JavaScript Module")
        
    except Exception as e:
        _LOGGER.debug("Could not reload resources: %s", e)
    
    hass.data[DOMAIN]["fishing_assistant_card_registered"] = True
