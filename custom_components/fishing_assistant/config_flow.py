"""Config flow for Fishing Assistant integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_MODE,
    MODE_FRESHWATER,
    MODE_OCEAN,
    CONF_NAME,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_FISH,
    CONF_BODY_TYPE,
    CONF_TIMEZONE,
    CONF_ELEVATION,
    CONF_HABITAT_PRESET,
    CONF_SPECIES_FOCUS,
    CONF_WEATHER_ENTITY,
    CONF_TIDE_MODE,
    CONF_TIDE_SENSOR,
    CONF_MARINE_ENABLED,
    CONF_AUTO_APPLY_THRESHOLDS,
    CONF_THRESHOLDS,
    TIDE_MODE_PROXY,
    TIDE_MODE_SENSOR,
    HABITAT_PRESETS,
)

_LOGGER = logging.getLogger(__name__)


class FishingAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fishing Assistant."""

    VERSION = 2

    def __init__(self):
        """Initialize the config flow."""
        self.ocean_config = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step - choose mode or use legacy freshwater."""
        # Check if this is a new setup or legacy
        if user_input is None:
            # Show mode selection for new setups
            return await self.async_step_mode_select()
        
        # Legacy freshwater setup (for backwards compatibility)
        return await self._async_step_freshwater(user_input)

    async def async_step_mode_select(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Select fishing mode."""
        errors = {}
        
        if user_input is not None:
            try:
                if user_input[CONF_MODE] == MODE_OCEAN:
                    return await self.async_step_ocean_location()
                else:
                    return await self.async_step_freshwater()
            except Exception as err:
                _LOGGER.error("Error in mode_select: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="mode_select",
            data_schema=vol.Schema({
                vol.Required(CONF_MODE, default=MODE_FRESHWATER): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": MODE_FRESHWATER, "label": "🎣 Freshwater (Lakes, Rivers, Ponds)"},
                            {"value": MODE_OCEAN, "label": "🌊 Ocean/Shore Fishing"},
                        ],
                        mode="list",
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_freshwater(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle freshwater fishing setup."""
        if user_input is not None:
            return await self._async_step_freshwater(user_input)

        return self.async_show_form(
            step_id="freshwater",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LATITUDE): cv.latitude,
                vol.Required(CONF_LONGITUDE): cv.longitude,
                vol.Required(CONF_FISH): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            "bass",
                            "pike",
                            "perch",
                            "trout",
                            "carp",
                            "catfish",
                            "walleye",
                            "crappie",
                        ],
                        multiple=True,
                        mode="dropdown",
                    )
                ),
                vol.Required(CONF_BODY_TYPE, default="lake"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["lake", "river", "pond"],
                        mode="dropdown",
                    )
                ),
            }),
        )

    async def _async_step_freshwater(self, user_input: dict[str, Any]) -> FlowResult:
        """Process freshwater setup."""
        errors = {}

        # Validate coordinates
        try:
            lat = float(user_input[CONF_LATITUDE])
            lon = float(user_input[CONF_LONGITUDE])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                errors["base"] = "invalid_coordinates"
        except (ValueError, KeyError):
            errors["base"] = "invalid_coordinates"

        if errors:
            return self.async_show_form(
                step_id="freshwater",
                data_schema=self._get_freshwater_schema(user_input),
                errors=errors,
            )

        # Add timezone and elevation (can be enhanced later)
        user_input[CONF_TIMEZONE] = str(self.hass.config.time_zone)
        user_input[CONF_ELEVATION] = self.hass.config.elevation
        user_input[CONF_MODE] = MODE_FRESHWATER

        return self.async_create_entry(
            title=user_input[CONF_NAME],
            data=user_input,
        )

    def _get_freshwater_schema(self, user_input: dict[str, Any] | None = None):
        """Get freshwater schema with defaults."""
        return vol.Schema({
            vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, "")): str,
            vol.Required(CONF_LATITUDE, default=user_input.get(CONF_LATITUDE, "")): cv.latitude,
            vol.Required(CONF_LONGITUDE, default=user_input.get(CONF_LONGITUDE, "")): cv.longitude,
            vol.Required(CONF_FISH, default=user_input.get(CONF_FISH, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        "bass",
                        "pike",
                        "perch",
                        "trout",
                        "carp",
                        "catfish",
                        "walleye",
                        "crappie",
                    ],
                    multiple=True,
                    mode="dropdown",
                )
            ),
            vol.Required(CONF_BODY_TYPE, default=user_input.get(CONF_BODY_TYPE, "lake")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["lake", "river", "pond"],
                    mode="dropdown",
                )
            ),
        })

    # ============================================================================
    # OCEAN MODE FLOW
    # ============================================================================

    async def async_step_ocean_location(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure ocean fishing location."""
        errors = {}
        
        if user_input is not None:
            # Validate coordinates
            try:
                lat = float(user_input[CONF_LATITUDE])
                lon = float(user_input[CONF_LONGITUDE])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    errors["base"] = "invalid_coordinates"
            except (ValueError, KeyError):
                errors["base"] = "invalid_coordinates"

            if not errors:
                self.ocean_config.update(user_input)
                return await self.async_step_ocean_habitat()

        # Get defaults - use HA config on first load, user_input on error
        default_name = user_input.get(CONF_NAME, "") if user_input else ""
        default_lat = user_input.get(CONF_LATITUDE, self.hass.config.latitude) if user_input else self.hass.config.latitude
        default_lon = user_input.get(CONF_LONGITUDE, self.hass.config.longitude) if user_input else self.hass.config.longitude

        return self.async_show_form(
            step_id="ocean_location",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=default_name): str,
                vol.Required(CONF_LATITUDE, default=default_lat): cv.latitude,
                vol.Required(CONF_LONGITUDE, default=default_lon): cv.longitude,
            }),
            errors=errors,
        )

    async def async_step_ocean_habitat(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure habitat and species."""
        if user_input is not None:
            self.ocean_config.update(user_input)
            return await self.async_step_ocean_weather()

        return self.async_show_form(
            step_id="ocean_habitat",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_HABITAT_PRESET,
                    default="rocky_point",
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "sandy_beach", "label": "🏖️ Sandy Beach"},
                            {"value": "rocky_point", "label": "🪨 Rocky Point/Jetty"},
                            {"value": "harbour", "label": "⚓ Harbour/Pier"},
                        ],
                        mode="list",
                    )
                ),
                vol.Required(
                    CONF_SPECIES_FOCUS,
                    default="general",
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "surf_predators", "label": "🦈 Surf Predators (Bass, Corbina)"},
                            {"value": "flatfish", "label": "🐟 Flatfish (Flounder, Sole)"},
                            {"value": "general", "label": "🎣 General (Mixed Species)"},
                        ],
                        mode="list",
                    )
                ),
                vol.Required(CONF_AUTO_APPLY_THRESHOLDS, default=True): selector.BooleanSelector(),
            }),
        )

    async def async_step_ocean_weather(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure weather integration."""
        if user_input is not None:
            if not user_input.get(CONF_WEATHER_ENTITY):
                return self.async_show_form(
                    step_id="ocean_weather",
                    data_schema=vol.Schema({
                        vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
                            selector.EntitySelectorConfig(domain="weather")
                        ),
                    }),
                    errors={"base": "no_weather_entity"},
                )

            self.ocean_config.update(user_input)
            return await self.async_step_ocean_data_sources()

        return self.async_show_form(
            step_id="ocean_weather",
            data_schema=vol.Schema({
                vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
            }),
        )

    async def async_step_ocean_data_sources(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure ocean data sources."""
        errors = {}
        
        if user_input is not None:
            # Validate tide sensor if selected
            if user_input.get(CONF_TIDE_MODE) == TIDE_MODE_SENSOR:
                if not user_input.get(CONF_TIDE_SENSOR):
                    errors["base"] = "no_tide_sensor"
            
            if not errors:
                self.ocean_config.update(user_input)
                return await self.async_step_ocean_thresholds()

        # Get current tide mode to determine if we show sensor selector
        tide_mode = user_input.get(CONF_TIDE_MODE, TIDE_MODE_PROXY) if user_input else TIDE_MODE_PROXY
        
        # Build schema dynamically
        schema_dict = {
            vol.Required(
                CONF_TIDE_MODE,
                default=tide_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": TIDE_MODE_PROXY, "label": "🌙 Automatic Tide Proxy (Recommended)"},
                        {"value": TIDE_MODE_SENSOR, "label": "📊 I have my own tide sensor"},
                    ],
                    mode="list",
                )
            ),
        }
        
        # Only show tide sensor selector if user chose custom sensor mode
        if tide_mode == TIDE_MODE_SENSOR:
            schema_dict[vol.Required(CONF_TIDE_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        
        # Add marine data toggle
        schema_dict[vol.Required(
            CONF_MARINE_ENABLED,
            default=user_input.get(CONF_MARINE_ENABLED, True) if user_input else True
        )] = selector.BooleanSelector()

        return self.async_show_form(
            step_id="ocean_data_sources",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_ocean_thresholds(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure safety thresholds."""
        if user_input is not None:
            # Build final config
            final_config = {
                CONF_MODE: MODE_OCEAN,
                CONF_NAME: self.ocean_config[CONF_NAME],
                CONF_LATITUDE: self.ocean_config[CONF_LATITUDE],
                CONF_LONGITUDE: self.ocean_config[CONF_LONGITUDE],
                CONF_HABITAT_PRESET: self.ocean_config[CONF_HABITAT_PRESET],
                CONF_SPECIES_FOCUS: self.ocean_config[CONF_SPECIES_FOCUS],
                CONF_AUTO_APPLY_THRESHOLDS: self.ocean_config[CONF_AUTO_APPLY_THRESHOLDS],
                CONF_WEATHER_ENTITY: self.ocean_config[CONF_WEATHER_ENTITY],
                CONF_TIDE_MODE: self.ocean_config[CONF_TIDE_MODE],
                CONF_MARINE_ENABLED: self.ocean_config[CONF_MARINE_ENABLED],
                CONF_THRESHOLDS: {
                    "max_wind_speed": user_input["max_wind_speed"],
                    "max_gust_speed": user_input["max_gust_speed"],
                    "max_wave_height": user_input["max_wave_height"],
                    "min_temperature": user_input["min_temperature"],
                    "max_temperature": user_input["max_temperature"],
                },
            }

            # Add tide sensor if using custom sensor
            if self.ocean_config[CONF_TIDE_MODE] == TIDE_MODE_SENSOR:
                final_config[CONF_TIDE_SENSOR] = self.ocean_config.get(CONF_TIDE_SENSOR)

            # Add timezone and elevation
            final_config[CONF_TIMEZONE] = str(self.hass.config.time_zone)
            final_config[CONF_ELEVATION] = self.hass.config.elevation

            return self.async_create_entry(
                title=self.ocean_config[CONF_NAME],
                data=final_config,
            )

        # Get defaults from habitat preset
        habitat = HABITAT_PRESETS.get(
            self.ocean_config.get(CONF_HABITAT_PRESET, "rocky_point"),
            HABITAT_PRESETS["rocky_point"]
        )

        return self.async_show_form(
            step_id="ocean_thresholds",
            data_schema=vol.Schema({
                vol.Required(
                    "max_wind_speed",
                    default=habitat.get("max_wind_speed", 25),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=50,
                        step=5,
                        unit_of_measurement="km/h",
                        mode="slider",
                    )
                ),
                vol.Required(
                    "max_gust_speed",
                    default=habitat.get("max_gust_speed", 40),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=15,
                        max=70,
                        step=5,
                        unit_of_measurement="km/h",
                        mode="slider",
                    )
                ),
                vol.Required(
                    "max_wave_height",
                    default=habitat.get("max_wave_height", 2.0),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=5.0,
                        step=0.5,
                        unit_of_measurement="m",
                        mode="slider",
                    )
                ),
                vol.Required(
                    "min_temperature",
                    default=5,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-10,
                        max=20,
                        step=1,
                        unit_of_measurement="°C",
                    )
                ),
                vol.Required(
                    "max_temperature",
                    default=35,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20,
                        max=50,
                        step=1,
                        unit_of_measurement="°C",
                    )
                ),
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Fishing Assistant."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        mode = self.config_entry.data.get(CONF_MODE, MODE_FRESHWATER)

        if mode == MODE_OCEAN:
            return await self.async_step_ocean_options()
        else:
            return await self.async_step_freshwater_options()

    async def async_step_freshwater_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle freshwater options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="freshwater_options",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_NAME,
                    default=self.config_entry.data.get(CONF_NAME, "")
                ): str,
                vol.Required(
                    CONF_LATITUDE,
                    default=self.config_entry.data.get(CONF_LATITUDE, "")
                ): cv.latitude,
                vol.Required(
                    CONF_LONGITUDE,
                    default=self.config_entry.data.get(CONF_LONGITUDE, "")
                ): cv.longitude,
            }),
        )

    async def async_step_ocean_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle ocean options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        thresholds = self.config_entry.data.get(CONF_THRESHOLDS, {})

        return self.async_show_form(
            step_id="ocean_options",
            data_schema=vol.Schema({
                vol.Required(
                    "max_wind_speed",
                    default=thresholds.get("max_wind_speed", 25),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=50,
                        step=5,
                        unit_of_measurement="km/h",
                        mode="slider",
                    )
                ),
                vol.Required(
                    "max_wave_height",
                    default=thresholds.get("max_wave_height", 2.0),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=5.0,
                        step=0.5,
                        unit_of_measurement="m",
                        mode="slider",
                    )
                ),
            }),
        )
