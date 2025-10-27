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
    CONF_SPECIES_ID,
    CONF_SPECIES_REGION,
    CONF_WEATHER_ENTITY,
    CONF_TIDE_MODE,
    CONF_TIDE_SENSOR,
    CONF_MARINE_ENABLED,
    CONF_AUTO_APPLY_THRESHOLDS,
    CONF_THRESHOLDS,
    CONF_TIME_PERIODS,
    CONF_USE_OPEN_METEO,
    TIDE_MODE_PROXY,
    TIDE_MODE_SENSOR,
    HABITAT_PRESETS,
    TIME_PERIODS_FULL_DAY,
    TIME_PERIODS_DAWN_DUSK,
    DEFAULT_NAME,
    HABITAT_ROCKY_POINT,
)
from .species_loader import SpeciesLoader

_LOGGER = logging.getLogger(__name__)


class FishingAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fishing Assistant."""

    VERSION = 2

    def __init__(self):
        """Initialize the config flow."""
        self.ocean_config = {}
        self.freshwater_config = {}
        self.species_loader = None

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
        """Handle freshwater fishing setup - location and species."""
        # Initialize species loader if not already done
        if self.species_loader is None:
            self.species_loader = SpeciesLoader(self.hass)
            await self.species_loader.async_load_profiles()

        if user_input is not None:
            # Validate coordinates
            errors = {}
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

            # Store config and move to time periods step
            self.freshwater_config.update(user_input)
            return await self.async_step_freshwater_time_periods()

        # Get freshwater species from JSON
        freshwater_species = self.species_loader.get_species_by_type("freshwater")

        # Check if species loaded successfully
        if not freshwater_species:
            _LOGGER.error("No freshwater species found in species_profiles.json")
            # Fallback to basic options
            species_options = [
                {"value": "bass", "label": "🐟 Bass"},
                {"value": "pike", "label": "🐟 Pike"},
                {"value": "trout", "label": "🐟 Trout"},
                {"value": "carp", "label": "🐟 Carp"},
            ]
        else:
            species_options = []
            for species in sorted(freshwater_species, key=lambda s: s.get("name", s["id"])):
                emoji = species.get("emoji", "🐟")
                name = species.get("name", species["id"])
                species_id = species["id"]

                # Add active months info
                active_months = species.get("active_months", [])
                if len(active_months) == 12:
                    season_info = "Year-round"
                elif len(active_months) > 0:
                    season_info = f"Active: {len(active_months)} months"
                else:
                    season_info = ""

                label = f"{emoji} {name}"
                if season_info:
                    label += f" ({season_info})"

                species_options.append({
                    "value": species_id,
                    "label": label
                })

        return self.async_show_form(
            step_id="freshwater",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LATITUDE): cv.latitude,
                vol.Required(CONF_LONGITUDE): cv.longitude,
                vol.Required(CONF_FISH): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=species_options,
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

    async def async_step_freshwater_time_periods(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure time period monitoring preference for freshwater."""
        if user_input is not None:
            self.freshwater_config.update(user_input)
            return await self.async_step_freshwater_weather()

        return self.async_show_form(
            step_id="freshwater_time_periods",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_TIME_PERIODS,
                    default=TIME_PERIODS_FULL_DAY,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": TIME_PERIODS_FULL_DAY,
                                "label": "🌅 Full Day (4 periods: Morning, Afternoon, Evening, Night)"
                            },
                            {
                                "value": TIME_PERIODS_DAWN_DUSK,
                                "label": "🌄 Dawn & Dusk Only (Prime fishing times around sunrise/sunset)"
                            },
                        ],
                        mode="list",
                    )
                ),
            }),
            description_placeholders={
                "info": "Choose which time periods to monitor. Dawn & Dusk focuses on the most productive fishing times."
            }
        )

    async def async_step_freshwater_weather(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure weather integration for freshwater (REQUIRED unless Open-Meteo chosen)."""
        if user_input is not None:
            use_open = bool(user_input.get(CONF_USE_OPEN_METEO, True))
            self.freshwater_config[CONF_USE_OPEN_METEO] = use_open

            # If user chose not to use Open-Meteo, require a weather entity
            if not use_open:
                if not user_input.get(CONF_WEATHER_ENTITY):
                    errors = {"base": "no_weather_entity"}
                    return self.async_show_form(
                        step_id="freshwater_weather",
                        data_schema=self._get_freshwater_weather_schema(user_input),
                        errors=errors,
                    )
                self.freshwater_config[CONF_WEATHER_ENTITY] = user_input[CONF_WEATHER_ENTITY]
            else:
                # If using Open-Meteo, weather entity is optional; only store if provided
                if user_input.get(CONF_WEATHER_ENTITY):
                    self.freshwater_config[CONF_WEATHER_ENTITY] = user_input[CONF_WEATHER_ENTITY]

            return await self.async_step_freshwater_thresholds()

        # Get available weather entities
        weather_entities = []
        for state in self.hass.states.async_all("weather"):
            weather_entities.append(state.entity_id)

        # Build options from available weather entities (may be empty)
        weather_options = []
        for entity in weather_entities:
            weather_options.append({"value": entity, "label": entity})

        return self.async_show_form(
            step_id="freshwater_weather",
            data_schema=self._get_freshwater_weather_schema(),
            description_placeholders={
                "info": "Select a Home Assistant weather entity to provide weather data for fishing conditions or enable Open‑Meteo. If Open‑Meteo is enabled, a weather entity is optional."
            }
        )

    def _get_freshwater_weather_schema(self, user_input: dict[str, Any] | None = None):
        """Schema builder for freshwater weather step (separate to allow re-use on errors)."""
        default_use_open = True
        default_weather = ""
        if user_input:
            default_use_open = user_input.get(CONF_USE_OPEN_METEO, default_use_open)
            default_weather = user_input.get(CONF_WEATHER_ENTITY, default_weather)

        return vol.Schema({
            vol.Required(CONF_USE_OPEN_METEO, default=default_use_open): selector.BooleanSelector(
                selector.BooleanSelectorConfig(
                    default=default_use_open,
                )
            ),
            vol.Optional(CONF_WEATHER_ENTITY, default=default_weather): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": s.entity_id, "label": s.entity_id} for s in self.hass.states.async_all("weather")] if self.hass else [],
                    mode="dropdown",
                )
            ),
        })

    async def async_step_freshwater_thresholds(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure safety thresholds for freshwater."""
        if user_input is not None:
            # Store thresholds
            self.freshwater_config[CONF_THRESHOLDS] = {
                "max_wind_speed": user_input["max_wind_speed"],
                "min_temperature": user_input["min_temperature"],
                "max_temperature": user_input["max_temperature"],
            }
            return await self._async_step_freshwater_complete()

        # Get defaults based on body type
        body_type = self.freshwater_config.get(CONF_BODY_TYPE, "lake")
        
        # Set defaults based on body type
        if body_type == "river":
            default_wind = 30
        elif body_type == "pond":
            default_wind = 35
        else:  # lake
            default_wind = 25

        return self.async_show_form(
            step_id="freshwater_thresholds",
            data_schema=vol.Schema({
                vol.Required(
                    "max_wind_speed",
                    default=default_wind,
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
                    "min_temperature",
                    default=0,
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
            description_placeholders={
                "info": "Set safe fishing limits for your comfort and safety."
            }
        )

    async def _async_step_freshwater_complete(self) -> FlowResult:
        """Complete freshwater setup."""
        # Add timezone and elevation
        self.freshwater_config[CONF_TIMEZONE] = str(self.hass.config.time_zone)
        self.freshwater_config[CONF_ELEVATION] = self.hass.config.elevation
        self.freshwater_config[CONF_MODE] = MODE_FRESHWATER

        # Ensure use_open_meteo defaults to True if not present (defensive)
        if CONF_USE_OPEN_METEO not in self.freshwater_config:
            self.freshwater_config[CONF_USE_OPEN_METEO] = True

        return self.async_create_entry(
            title=self.freshwater_config.get(CONF_NAME, DEFAULT_NAME),
            data=self.freshwater_config,
        )

    async def _async_step_freshwater(self, user_input: dict[str, Any]) -> FlowResult:
        """Process freshwater setup (legacy method for backwards compatibility)."""
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

        # Add default time period if not present
        if CONF_TIME_PERIODS not in user_input:
            user_input[CONF_TIME_PERIODS] = TIME_PERIODS_FULL_DAY

        # Default use_open_meteo to True if not present
        if CONF_USE_OPEN_METEO not in user_input:
            user_input[CONF_USE_OPEN_METEO] = True

        # Check if weather entity exists when Open-Meteo not chosen - if not, abort
        if not user_input.get(CONF_USE_OPEN_METEO, True) and CONF_WEATHER_ENTITY not in user_input:
            _LOGGER.error("Legacy config missing weather entity and Open-Meteo disabled")
            errors["base"] = "no_weather_entity"
            return self.async_show_form(
                step_id="freshwater",
                data_schema=self._get_freshwater_schema(user_input),
                errors=errors,
            )

        return self.async_create_entry(
            title=user_input.get(CONF_NAME, DEFAULT_NAME),
            data=user_input,
        )

    def _get_freshwater_schema(self, user_input: dict[str, Any] | None = None):
        """Get freshwater schema with defaults - used for error handling."""
        # Fallback schema with basic species
        species_options = [
            {"value": "bass", "label": "🐟 Bass"},
            {"value": "pike", "label": "🐟 Pike"},
            {"value": "trout", "label": "🐟 Trout"},
            {"value": "carp", "label": "🐟 Carp"},
            {"value": "catfish", "label": "🐟 Catfish"},
            {"value": "perch", "label": "🐟 Perch"},
            {"value": "walleye", "label": "🐟 Walleye"},
            {"value": "crappie", "label": "🐟 Crappie"},
        ]

        # If species loader is available, use it
        if self.species_loader and self.species_loader._profiles:
            freshwater_species = self.species_loader.get_species_by_type("freshwater")
            if freshwater_species:
                species_options = []
                for species in sorted(freshwater_species, key=lambda s: s.get("name", s["id"])):
                    emoji = species.get("emoji", "🐟")
                    name = species.get("name", species["id"])
                    species_id = species["id"]

                    active_months = species.get("active_months", [])
                    if len(active_months) == 12:
                        season_info = "Year-round"
                    elif len(active_months) > 0:
                        season_info = f"Active: {len(active_months)} months"
                    else:
                        season_info = ""

                    label = f"{emoji} {name}"
                    if season_info:
                        label += f" ({season_info})"

                    species_options.append({
                        "value": species_id,
                        "label": label
                    })

        return vol.Schema({
            vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, "") if user_input else ""): str,
            vol.Required(CONF_LATITUDE, default=user_input.get(CONF_LATITUDE, "") if user_input else ""): cv.latitude,
            vol.Required(CONF_LONGITUDE, default=user_input.get(CONF_LONGITUDE, "") if user_input else ""): cv.longitude,
            vol.Required(CONF_FISH, default=user_input.get(CONF_FISH, []) if user_input else []): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=species_options,
                    multiple=True,
                    mode="dropdown",
                )
            ),
            vol.Required(CONF_BODY_TYPE, default=user_input.get(CONF_BODY_TYPE, "lake") if user_input else "lake"): selector.SelectSelector(
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
                return await self.async_step_ocean_species()

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

    async def async_step_ocean_species(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure species/region selection - combined step."""
        # Initialize species loader if not already done
        if self.species_loader is None:
            self.species_loader = SpeciesLoader(self.hass)
            await self.species_loader.async_load_profiles()

        if user_input is not None:
            # Extract species_id and determine region from the selection
            species_id = user_input[CONF_SPECIES_ID]

            # Check if this is a general_mixed selection
            if species_id.startswith("general_mixed_"):
                # Extract region from the ID
                species_region = species_id.replace("general_mixed_", "")
                species_id = "general_mixed"
            else:
                # Find which region this species belongs to
                species_profile = self.species_loader.get_species(species_id)
                if species_profile:
                    # Use the first region in the list as primary
                    available_regions = species_profile.get("regions", ["global"])
                    species_region = available_regions[0]
                else:
                    species_region = "global"

            self.ocean_config[CONF_SPECIES_ID] = species_id
            self.ocean_config[CONF_SPECIES_REGION] = species_region

            return await self.async_step_ocean_habitat()

        # Build a comprehensive species list organized by region
        regions = self.species_loader.get_regions_by_type("ocean")
        species_options = []

        # === SECTION 1: GENERAL REGION PROFILES ===
        species_options.append({
            "value": "separator_regions",
            "label": "━━━━━ 🎣 GENERAL REGION PROFILES ━━━━━"
        })

        for region in regions:
            region_id = region["id"]
            region_name = region["name"]

            # Add a "General Mixed" option for each region
            species_options.append({
                "value": f"general_mixed_{region_id}",
                "label": f"🎣 {region_name} - General Mixed Species"
            })

        # === SECTION 2: SPECIFIC SPECIES ===
        species_options.append({
            "value": "separator_species",
            "label": "━━━━━ 🐟 TARGET SPECIFIC SPECIES ━━━━━"
        })

        # Collect all ocean species from all regions (excluding global)
        all_species = []
        for region in regions:
            region_id = region["id"]

            # Skip global region for species listing (it only has general profiles)
            if region_id == "global":
                continue

            # Get all species for this region
            species_list = self.species_loader.get_species_by_region(region_id)

            # Filter out general profiles and add to collection
            for species in species_list:
                # Only include ocean species
                if species.get("type") != "ocean":
                    continue

                if (not species["id"].startswith("general_mixed")
                        and not species["id"].startswith("surf_predators")
                        and not species["id"].startswith("flatfish")):
                    # Check if we already have this species (avoid duplicates)
                    if not any(s["id"] == species["id"] for s in all_species):
                        all_species.append(species)

        # Sort species alphabetically by name
        all_species.sort(key=lambda s: s.get("name", s["id"]))

        # Add sorted species to options
        for species in all_species:
            emoji = species.get("emoji", "🐟")
            name = species.get("name", species["id"])
            species_id = species["id"]

            # Add active months info
            active_months = species.get("active_months", [])
            if len(active_months) == 12:
                season_info = "Year-round"
            elif len(active_months) > 0:
                season_info = f"Active: {len(active_months)} months"
            else:
                season_info = ""

            label = f"{emoji} {name}"
            if season_info:
                label += f" ({season_info})"

            species_options.append({"value": species_id, "label": label})

        return self.async_show_form(
            step_id="ocean_species",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SPECIES_ID,
                    default="general_mixed_gibraltar",  # Default to Gibraltar general
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=species_options,
                        mode="dropdown",
                    )
                ),
            }),
            description_placeholders={
                "info": "Choose a general region profile for mixed species, or target a specific species."
            }
        )

    async def async_step_ocean_habitat(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure habitat."""
        if user_input is not None:
            self.ocean_config.update(user_input)
            return await self.async_step_ocean_weather()

        return self.async_show_form(
            step_id="ocean_habitat",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_HABITAT_PRESET,
                    default=HABITAT_ROCKY_POINT,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "open_beach", "label": "🏖️ Open Sandy Beach"},
                            {"value": "rocky_point", "label": "🪨 Rocky Point/Jetty"},
                            {"value": "harbour", "label": "⚓ Harbour/Pier"},
                            {"value": "reef", "label": "🪸 Offshore Reef"},
                        ],
                        mode="list",
                    )
                ),
            }),
        )

    async def async_step_ocean_weather(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure weather integration (REQUIRED unless Open-Meteo chosen)."""
        if user_input is not None:
            use_open = bool(user_input.get(CONF_USE_OPEN_METEO, True))
            self.ocean_config[CONF_USE_OPEN_METEO] = use_open

            # If user chose not to use Open-Meteo, require a weather entity
            if not use_open:
                if not user_input.get(CONF_WEATHER_ENTITY):
                    errors = {"base": "no_weather_entity"}
                    return self.async_show_form(
                        step_id="ocean_weather",
                        data_schema=self._get_ocean_weather_schema(user_input),
                        errors=errors,
                    )
                self.ocean_config[CONF_WEATHER_ENTITY] = user_input[CONF_WEATHER_ENTITY]
            else:
                if user_input.get(CONF_WEATHER_ENTITY):
                    self.ocean_config[CONF_WEATHER_ENTITY] = user_input[CONF_WEATHER_ENTITY]

            # Set defaults for tide and marine data if not set elsewhere
            self.ocean_config[CONF_TIDE_MODE] = self.ocean_config.get(CONF_TIDE_MODE, TIDE_MODE_PROXY)
            self.ocean_config[CONF_MARINE_ENABLED] = self.ocean_config.get(CONF_MARINE_ENABLED, True)

            return await self.async_step_ocean_time_periods()

        # Build options for weather entities (may be empty)
        weather_entities = []
        for state in self.hass.states.async_all("weather"):
            weather_entities.append(state.entity_id)

        weather_options = [{"value": e, "label": e} for e in weather_entities]

        return self.async_show_form(
            step_id="ocean_weather",
            data_schema=self._get_ocean_weather_schema(),
            description_placeholders={
                "info": "Select a Home Assistant weather entity or enable Open‑Meteo marine API. If Open‑Meteo is enabled, a weather entity is optional."
            }
        )

    def _get_ocean_weather_schema(self, user_input: dict[str, Any] | None = None):
        """Schema builder for ocean weather step (used for re-showing after validation errors)."""
        default_use_open = True
        default_weather = ""
        if user_input:
            default_use_open = user_input.get(CONF_USE_OPEN_METEO, default_use_open)
            default_weather = user_input.get(CONF_WEATHER_ENTITY, default_weather)

        return vol.Schema({
            vol.Required(CONF_USE_OPEN_METEO, default=default_use_open): selector.BooleanSelector(
                selector.BooleanSelectorConfig(
                    default=default_use_open,
                )
            ),
            vol.Optional(CONF_WEATHER_ENTITY, default=default_weather): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": s.entity_id, "label": s.entity_id} for s in self.hass.states.async_all("weather")] if self.hass else [],
                    mode="dropdown",
                )
            ),
        })

    async def async_step_ocean_time_periods(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure time period monitoring preference."""
        if user_input is not None:
            _LOGGER.debug("async_step_ocean_time_periods - received user_input: %s", user_input)
            errors = {}
            try:
                # Basic validation: ensure time_periods is present and valid
                tp = user_input.get(CONF_TIME_PERIODS)
                valid = {TIME_PERIODS_FULL_DAY, TIME_PERIODS_DAWN_DUSK}
                if tp is None:
                    errors["base"] = "missing_time_periods"
                elif tp not in valid:
                    _LOGGER.warning("Invalid time_periods submitted: %s (valid=%s)", tp, valid)
                    errors["base"] = "invalid_time_periods"

                if errors:
                    return self.async_show_form(
                        step_id="ocean_time_periods",
                        data_schema=vol.Schema({
                            vol.Required(
                                CONF_TIME_PERIODS,
                                default=self.ocean_config.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=[
                                        {
                                            "value": TIME_PERIODS_FULL_DAY,
                                            "label": "🌅 Full Day (4 periods: Morning, Afternoon, Evening, Night)"
                                        },
                                        {
                                            "value": TIME_PERIODS_DAWN_DUSK,
                                            "label": "🌄 Dawn & Dusk Only (Prime fishing times: ±1hr sunrise/sunset)"
                                        },
                                    ],
                                    mode="list",
                                )
                            ),
                        }),
                        errors=errors,
                        description_placeholders={
                            "info": "Choose which time periods to monitor. Dawn & Dusk focuses on the most productive fishing times."
                        }
                    )

                # Store and continue
                self.ocean_config.update(user_input)
                return await self.async_step_ocean_thresholds()

            except Exception as exc:
                _LOGGER.exception("Unhandled exception in async_step_ocean_time_periods: %s", exc)
                # Re-show the form with a general error while preserving current defaults
                return self.async_show_form(
                    step_id="ocean_time_periods",
                    data_schema=vol.Schema({
                        vol.Required(
                            CONF_TIME_PERIODS,
                            default=self.ocean_config.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY),
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {
                                        "value": TIME_PERIODS_FULL_DAY,
                                        "label": "🌅 Full Day (4 periods: Morning, Afternoon, Evening, Night)"
                                    },
                                    {
                                        "value": TIME_PERIODS_DAWN_DUSK,
                                        "label": "🌄 Dawn & Dusk Only (Prime fishing times: ±1hr sunrise/sunset)"
                                    },
                                ],
                                mode="list",
                            )
                        ),
                    }),
                    errors={"base": "unknown"},
                    description_placeholders={
                        "info": "Choose which time periods to monitor. Dawn & Dusk focuses on the most productive fishing times."
                    }
                )

        return self.async_show_form(
            step_id="ocean_time_periods",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_TIME_PERIODS,
                    default=TIME_PERIODS_FULL_DAY,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": TIME_PERIODS_FULL_DAY,
                                "label": "🌅 Full Day (4 periods: Morning, Afternoon, Evening, Night)"
                            },
                            {
                                "value": TIME_PERIODS_DAWN_DUSK,
                                "label": "🌄 Dawn & Dusk Only (Prime fishing times: ±1hr sunrise/sunset)"
                            },
                        ],
                        mode="list",
                    )
                ),
            }),
            description_placeholders={
                "info": "Choose which time periods to monitor. Dawn & Dusk focuses on the most productive fishing times."
            }
        )

    async def async_step_ocean_thresholds(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure safety thresholds."""
        if user_input is not None:
            # Defensive: ensure required previous keys exist and coerce/validate types
            try:
                # Name
                name = self.ocean_config.get(CONF_NAME) or DEFAULT_NAME

                # Latitude / Longitude (coerce to float; if missing, default to HA config coords)
                try:
                    lat_raw = self.ocean_config.get(CONF_LATITUDE, self.hass.config.latitude)
                    latitude = float(lat_raw)
                except Exception:
                    _LOGGER.warning("Invalid latitude in ocean_config: %s; using HA default", lat_raw)
                    latitude = float(self.hass.config.latitude)

                try:
                    lon_raw = self.ocean_config.get(CONF_LONGITUDE, self.hass.config.longitude)
                    longitude = float(lon_raw)
                except Exception:
                    _LOGGER.warning("Invalid longitude in ocean_config: %s; using HA default", lon_raw)
                    longitude = float(self.hass.config.longitude)

                habitat_preset = self.ocean_config.get(CONF_HABITAT_PRESET, HABITAT_ROCKY_POINT)

                final_config = {
                    CONF_MODE: MODE_OCEAN,
                    CONF_NAME: name,
                    CONF_LATITUDE: latitude,
                    CONF_LONGITUDE: longitude,
                    CONF_SPECIES_ID: self.ocean_config.get(CONF_SPECIES_ID, "general_mixed"),
                    CONF_SPECIES_REGION: self.ocean_config.get(CONF_SPECIES_REGION, "global"),
                    CONF_HABITAT_PRESET: habitat_preset,
                    CONF_TIME_PERIODS: self.ocean_config.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY),
                    CONF_AUTO_APPLY_THRESHOLDS: False,  # Always show thresholds
                    CONF_TIDE_MODE: TIDE_MODE_PROXY,  # Always use proxy
                    CONF_MARINE_ENABLED: True,  # Always enabled
                    CONF_WEATHER_ENTITY: self.ocean_config.get(CONF_WEATHER_ENTITY),
                    CONF_USE_OPEN_METEO: self.ocean_config.get(CONF_USE_OPEN_METEO, True),
                    CONF_THRESHOLDS: {
                        "max_wind_speed": user_input["max_wind_speed"],
                        "max_gust_speed": user_input["max_gust_speed"],
                        "max_wave_height": user_input["max_wave_height"],
                        "min_temperature": user_input["min_temperature"],
                        "max_temperature": user_input["max_temperature"],
                    },
                }

                # Add timezone and elevation
                final_config[CONF_TIMEZONE] = str(self.hass.config.time_zone)
                final_config[CONF_ELEVATION] = self.hass.config.elevation

                _LOGGER.debug("Creating ocean config entry with data keys: %s", list(final_config.keys()))

                return self.async_create_entry(
                    title=name,
                    data=final_config,
                )
            except KeyError as ke:
                _LOGGER.exception("Missing expected key when building final ocean config: %s", ke)
                # Re-show thresholds form with a general error so user can try again
                return self.async_show_form(
                    step_id="ocean_thresholds",
                    data_schema=vol.Schema({
                        vol.Required(
                            "max_wind_speed",
                            default=25,
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
                            default=40,
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
                            default=2.0,
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
                    errors={"base": "unknown"},
                )
            except Exception as exc:
                _LOGGER.exception("Unhandled exception in async_step_ocean_thresholds: %s", exc)
                return self.async_show_form(
                    step_id="ocean_thresholds",
                    data_schema=vol.Schema({
                        vol.Required(
                            "max_wind_speed",
                            default=25,
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
                            default=40,
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
                            default=2.0,
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
                    errors={"base": "unknown"},
                )

        # When showing the form for the first time, use habitat preset defaults
        habitat = HABITAT_PRESETS.get(
            self.ocean_config.get(CONF_HABITAT_PRESET, HABITAT_ROCKY_POINT),
            HABITAT_PRESETS.get(HABITAT_ROCKY_POINT, {})
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
            description_placeholders={
                "info": "Set safe fishing limits based on your habitat and comfort level."
            }
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
                vol.Required(
                    CONF_TIME_PERIODS,
                    default=self.config_entry.data.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": TIME_PERIODS_FULL_DAY,
                                "label": "🌅 Full Day (4 periods)"
                            },
                            {
                                "value": TIME_PERIODS_DAWN_DUSK,
                                "label": "🌄 Dawn & Dusk Only"
                            },
                        ],
                        mode="dropdown",
                    )
                ),
                vol.Required(
                    CONF_USE_OPEN_METEO,
                    default=self.config_entry.data.get(CONF_USE_OPEN_METEO, True),
                ): selector.BooleanSelector(
                    selector.BooleanSelectorConfig(
                        default=self.config_entry.data.get(CONF_USE_OPEN_METEO, True),
                    )
                ),
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
                    CONF_TIME_PERIODS,
                    default=self.config_entry.data.get(CONF_TIME_PERIODS, TIME_PERIODS_FULL_DAY)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": TIME_PERIODS_FULL_DAY,
                                "label": "🌅 Full Day (4 periods)"
                            },
                            {
                                "value": TIME_PERIODS_DAWN_DUSK,
                                "label": "🌄 Dawn & Dusk Only"
                            },
                        ],
                        mode="dropdown",
                    )
                ),
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
                vol.Required(
                    CONF_USE_OPEN_METEO,
                    default=self.config_entry.data.get(CONF_USE_OPEN_METEO, True),
                ): selector.BooleanSelector(
                    selector.BooleanSelectorConfig(
                        default=self.config_entry.data.get(CONF_USE_OPEN_METEO, True),
                    )
                ),
            }),
        )