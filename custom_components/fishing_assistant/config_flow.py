from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    # Ocean mode constants
    CONF_MODE,
    MODE_FRESHWATER,
    MODE_OCEAN,
    CONF_WEATHER_ENTITY,
    CONF_MARINE_ENABLED,
    CONF_TIDE_MODE,
    CONF_HABITAT_PRESET,
    CONF_SPECIES_FOCUS,
    CONF_THRESHOLDS,
    TIDE_MODE_PROXY,
    TIDE_MODE_CUSTOM,
    HABITAT_PRESETS,
    SPECIES_FOCUS,
    DEFAULT_OCEAN_THRESHOLDS,
)
from .helpers.location import resolve_location_metadata_sync
from .fish_profiles import get_fish_species


class FishingAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Fishing Assistant config flow."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self.data = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step - choose mode."""
        if user_input is not None:
            mode = user_input.get(CONF_MODE, MODE_FRESHWATER)
            self.data[CONF_MODE] = mode
            
            if mode == MODE_OCEAN:
                return await self.async_step_ocean_location()
            else:
                return await self.async_step_freshwater()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_MODE, default=MODE_FRESHWATER): vol.In({
                    MODE_FRESHWATER: "🎣 Freshwater (Lakes/Rivers/Ponds)",
                    MODE_OCEAN: "🌊 Ocean/Shore Fishing (Beta)",
                }),
            }),
            description_placeholders={
                "info": (
                    "Ocean Mode adds tide predictions, wave data, and marine weather scoring. "
                    "Perfect for coastal and shore fishing!"
                )
            },
        )

    async def async_step_freshwater(self, user_input=None):
        """Handle freshwater mode (original flow)."""
        errors = {}
        
        if user_input is not None:
            name = user_input["name"]
            lat = user_input["latitude"]
            lon = user_input["longitude"]
            fish = user_input["fish"]
            body_type = user_input["body_type"]

            await self.async_set_unique_id(f"{lat:.5f}_{lon:.5f}")
            self._abort_if_unique_id_configured()

            metadata = await self.hass.async_add_executor_job(
                resolve_location_metadata_sync, lat, lon
            )

            return self.async_create_entry(
                title=name,
                data={
                    "mode": MODE_FRESHWATER,
                    "name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "fish": fish,
                    "body_type": body_type,
                    "elevation": metadata.get("elevation"),
                    "timezone": metadata.get("timezone"),
                },
            )

        return self.async_show_form(
            step_id="freshwater",
            data_schema=vol.Schema({
                vol.Required("name"): str,
                vol.Required("latitude"): vol.Coerce(float),
                vol.Required("longitude"): vol.Coerce(float),
                vol.Required("fish"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": f, "label": f.replace("_", " ").title()}
                            for f in sorted(get_fish_species())
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required("body_type"): vol.In(["lake", "river", "pond", "reservoir"]),
            }),
            errors=errors
        )

    # ========================================================================
    # OCEAN MODE FLOW
    # ========================================================================

    async def async_step_ocean_location(self, user_input=None):
        """Configure ocean fishing location."""
        errors = {}

        if user_input is not None:
            # Validate coordinates
            try:
                lat = float(user_input["latitude"])
                lon = float(user_input["longitude"])
                
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    errors["base"] = "invalid_coordinates"
                else:
                    self.data.update(user_input)
                    self.data["latitude"] = lat
                    self.data["longitude"] = lon
                    return await self.async_step_ocean_tide()
                    
            except ValueError:
                errors["base"] = "invalid_coordinates"

        # Get HA's home location as default
        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude

        # Get available weather entities
        weather_entities = self._get_weather_entities()
        if not weather_entities:
            weather_entities = {"none": "No weather entities found"}

        return self.async_show_form(
            step_id="ocean_location",
            data_schema=vol.Schema({
                vol.Required("name", default="Ocean Fishing Spot"): str,
                vol.Required("latitude", default=home_lat): vol.Coerce(float),
                vol.Required("longitude", default=home_lon): vol.Coerce(float),
                vol.Required(CONF_WEATHER_ENTITY): vol.In(weather_entities),
            }),
            errors=errors,
            description_placeholders={
                "info": (
                    "Enter your fishing spot coordinates. "
                    "Weather entity should be Met.no or similar for best results."
                )
            },
        )

    async def async_step_ocean_tide(self, user_input=None):
        """Configure tide data source."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_ocean_habitat()

        return self.async_show_form(
            step_id="ocean_tide",
            data_schema=vol.Schema({
                vol.Required(CONF_TIDE_MODE, default=TIDE_MODE_PROXY): vol.In({
                    TIDE_MODE_PROXY: "🌙 Automatic Tide Proxy (Recommended)",
                    TIDE_MODE_CUSTOM: "📊 I have my own tide sensor",
                }),
                vol.Optional(CONF_MARINE_ENABLED, default=True): cv.boolean,
            }),
            description_placeholders={
                "info": (
                    "🌙 Tide Proxy: Uses sun/moon positions to estimate tides worldwide (no API needed).\n"
                    "📊 Marine Data: Adds wave height/period from Open-Meteo (free, no API key).\n\n"
                    "Recommended: Enable both for best results!"
                ),
            },
        )

    async def async_step_ocean_habitat(self, user_input=None):
        """Configure habitat and species focus."""
        if user_input is not None:
            self.data.update(user_input)
            
            # Get habitat preset to apply thresholds
            habitat_key = user_input[CONF_HABITAT_PRESET]
            habitat = HABITAT_PRESETS[habitat_key]
            
            # Merge default thresholds with habitat-specific ones
            thresholds = DEFAULT_OCEAN_THRESHOLDS.copy()
            thresholds.update({
                "max_wind_speed": habitat["max_wind_speed"],
                "max_gust_speed": habitat["max_gust_speed"],
                "max_wave_height": habitat["max_wave_height"],
            })
            self.data[CONF_THRESHOLDS] = thresholds
            
            # Set unique ID
            lat = self.data["latitude"]
            lon = self.data["longitude"]
            await self.async_set_unique_id(f"{lat:.5f}_{lon:.5f}_ocean")
            self._abort_if_unique_id_configured()
            
            # Get location metadata
            metadata = await self.hass.async_add_executor_job(
                resolve_location_metadata_sync, lat, lon
            )
            self.data["elevation"] = metadata.get("elevation")
            self.data["timezone"] = metadata.get("timezone")
            
            # Create the entry
            habitat_name = HABITAT_PRESETS[habitat_key]["name"]
            return self.async_create_entry(
                title=f"{self.data['name']} ({habitat_name})",
                data=self.data,
            )

        # Build habitat options with descriptions
        habitat_options = [
            {
                "value": k,
                "label": f"{v['name']} - {v['description']}"
            }
            for k, v in HABITAT_PRESETS.items()
        ]

        # Build species options with descriptions
        species_options = [
            {
                "value": k,
                "label": f"{v['name']} - {v['description']}"
            }
            for k, v in SPECIES_FOCUS.items()
        ]

        return self.async_show_form(
            step_id="ocean_habitat",
            data_schema=vol.Schema({
                vol.Required(CONF_HABITAT_PRESET, default=HABITAT_OPEN_BEACH): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=habitat_options,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                vol.Required(CONF_SPECIES_FOCUS, default=SPECIES_GENERAL): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=species_options,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
            }),
            description_placeholders={
                "info": (
                    "🏖️ Habitat: Affects wave/wind safety thresholds and scoring weights.\n"
                    "🐟 Species Focus: Adjusts scoring based on target fish behavior and preferences.\n\n"
                    "You can change these later in integration options."
                ),
            },
        )

    def _get_weather_entities(self):
        """Get list of weather entities."""
        weather_entities = {}
        for state in self.hass.states.async_all("weather"):
            friendly_name = state.attributes.get("friendly_name", state.entity_id)
            weather_entities[state.entity_id] = friendly_name
        return weather_entities

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return FishingAssistantOptionsFlow(config_entry)

    @staticmethod
    @callback
    def async_get_entry_title(entry: config_entries.ConfigEntry) -> str:
        """Return the title of the config entry shown in the UI."""
        return entry.data.get("name", "Fishing Location")


class FishingAssistantOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Fishing Assistant."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        mode = self.config_entry.data.get(CONF_MODE, MODE_FRESHWATER)
        
        if mode == MODE_OCEAN:
            return await self.async_step_ocean_options(user_input)
        else:
            return await self.async_step_freshwater_options(user_input)

    async def async_step_freshwater_options(self, user_input=None):
        """Handle freshwater options (original)."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="freshwater_options",
            data_schema=vol.Schema({
                vol.Required("fish", default=self.config_entry.data.get("fish", [])): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": f, "label": f.replace("_", " ").title()}
                                for f in sorted(get_fish_species())
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                vol.Required("body_type", default=self.config_entry.data.get("body_type", "lake")):
                    vol.In(["lake", "river", "pond", "reservoir"]),
            })
        )

    async def async_step_ocean_options(self, user_input=None):
        """Handle ocean mode options."""
        if user_input is not None:
            # Update thresholds based on habitat change
            habitat_key = user_input[CONF_HABITAT_PRESET]
            habitat = HABITAT_PRESETS[habitat_key]
            
            thresholds = self.config_entry.data.get(CONF_THRESHOLDS, DEFAULT_OCEAN_THRESHOLDS).copy()
            thresholds.update({
                "max_wind_speed": habitat["max_wind_speed"],
                "max_gust_speed": habitat["max_gust_speed"],
                "max_wave_height": habitat["max_wave_height"],
            })
            user_input[CONF_THRESHOLDS] = thresholds
            
            return self.async_create_entry(title="", data=user_input)

        # Build options
        habitat_options = [
            {"value": k, "label": v["name"]}
            for k, v in HABITAT_PRESETS.items()
        ]

        species_options = [
            {"value": k, "label": v["name"]}
            for k, v in SPECIES_FOCUS.items()
        ]

        current_habitat = self.config_entry.data.get(CONF_HABITAT_PRESET, HABITAT_OPEN_BEACH)
        current_species = self.config_entry.data.get(CONF_SPECIES_FOCUS, SPECIES_GENERAL)
        current_marine = self.config_entry.data.get(CONF_MARINE_ENABLED, True)

        return self.async_show_form(
            step_id="ocean_options",
            data_schema=vol.Schema({
                vol.Required(CONF_HABITAT_PRESET, default=current_habitat): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=habitat_options,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                vol.Required(CONF_SPECIES_FOCUS, default=current_species): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=species_options,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                vol.Optional(CONF_MARINE_ENABLED, default=current_marine): cv.boolean,
            }),
            description_placeholders={
                "info": "Update your ocean fishing preferences. Changes take effect immediately."
            },
        )
