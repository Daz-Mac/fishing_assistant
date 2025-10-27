DOMAIN = "fishing_assistant"
DEFAULT_NAME = "Fishing Assistant"
CONF_USE_OPEN_METEO = "use_open_meteo"

# ============================================================================
# OCEAN MODE CONSTANTS (additions for ocean/shore fishing)
# ============================================================================

# Configuration keys for Ocean Mode
CONF_MODE = "mode"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_MARINE_ENABLED = "marine_enabled"
CONF_TIDE_MODE = "tide_mode"
CONF_TIDE_ENTITIES = "tide_entities"
CONF_HABITAT_PRESET = "habitat_preset"
CONF_SPECIES_FOCUS = "species_focus"
CONF_THRESHOLDS = "thresholds"

CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_FISH = "fish"
CONF_BODY_TYPE = "body_type"
CONF_TIMEZONE = "timezone"
CONF_ELEVATION = "elevation"
CONF_TIDE_SENSOR = "tide_sensor"
CONF_AUTO_APPLY_THRESHOLDS = "auto_apply_thresholds"
CONF_SPECIES_ID = "species_id"
CONF_SPECIES_REGION = "species_region"
CONF_TIME_PERIODS = "time_periods"

# Mode options
MODE_FRESHWATER = "freshwater"
MODE_OCEAN = "ocean"

# Tide mode options
TIDE_MODE_PROXY = "proxy"
TIDE_MODE_CUSTOM = "custom"
TIDE_MODE_UKHO = "ukho_api"
TIDE_MODE_NOAA = "noaa_api"
TIDE_MODE_WORLDTIDES = "worldtides_api"
TIDE_MODE_SENSOR = "sensor"

# Time period options
TIME_PERIODS_FULL_DAY = "full_day"
TIME_PERIODS_DAWN_DUSK = "dawn_dusk"

# Aliases for period types (used in scoring modules)
PERIOD_FULL_DAY = TIME_PERIODS_FULL_DAY
PERIOD_DAWN_DUSK = TIME_PERIODS_DAWN_DUSK

# Habitat presets for ocean fishing
HABITAT_OPEN_BEACH = "open_beach"
HABITAT_ROCKY_POINT = "rocky_point"
HABITAT_HARBOUR = "harbour"
HABITAT_REEF = "reef"

# Freshwater body type constants (used as habitat equivalents)
BODY_TYPE_LAKE = "lake"
BODY_TYPE_RIVER = "river"
BODY_TYPE_POND = "pond"

HABITAT_PRESETS = {
    HABITAT_OPEN_BEACH: {
        "name": "Open Sandy Beach",
        "description": "Exposed sandy beach with surf",
        "max_wave_height": 2.0,  # meters
        "max_wind_speed": 25,  # km/h
        "max_gust_speed": 40,
        "wave_weight": 0.3,  # Importance in scoring
        "tide_weight": 0.4,
        "wind_weight": 0.2,
        "safety_critical": True,
    },
    HABITAT_ROCKY_POINT: {
        "name": "Rocky Point/Reef",
        "description": "Rocky outcrop or reef structure",
        "max_wave_height": 3.0,
        "max_wind_speed": 30,
        "max_gust_speed": 45,
        "wave_weight": 0.2,
        "tide_weight": 0.3,
        "wind_weight": 0.15,
        "safety_critical": True,
    },
    HABITAT_HARBOUR: {
        "name": "Harbour/Breakwater",
        "description": "Protected harbour or breakwater",
        "max_wave_height": 1.5,
        "max_wind_speed": 35,
        "max_gust_speed": 50,
        "wave_weight": 0.1,
        "tide_weight": 0.2,
        "wind_weight": 0.1,
        "safety_critical": False,
    },
    HABITAT_REEF: {
        "name": "Offshore Reef",
        "description": "Offshore reef or structure",
        "max_wave_height": 2.5,
        "max_wind_speed": 20,
        "max_gust_speed": 35,
        "wave_weight": 0.4,
        "tide_weight": 0.3,
        "wind_weight": 0.25,
        "safety_critical": True,
    },
    # Freshwater body types
    BODY_TYPE_LAKE: {
        "name": "Lake",
        "description": "Open lake fishing",
        "max_wind_speed": 25,  # km/h
        "max_gust_speed": 40,  # km/h (estimated from wind)
        "max_wave": 0.5,  # meters (estimated from wind)
        "wind_weight": 0.3,
        "safety_critical": True,
    },
    BODY_TYPE_RIVER: {
        "name": "River",
        "description": "River or stream fishing",
        "max_wind_speed": 30,  # km/h
        "max_gust_speed": 45,  # km/h (estimated from wind)
        "max_wave": 0.3,  # meters (minimal waves in rivers)
        "wind_weight": 0.2,
        "safety_critical": False,
    },
    BODY_TYPE_POND: {
        "name": "Pond",
        "description": "Small pond or protected water",
        "max_wind_speed": 35,  # km/h
        "max_gust_speed": 50,  # km/h (estimated from wind)
        "max_wave": 0.2,  # meters (minimal waves in ponds)
        "wind_weight": 0.1,
        "safety_critical": False,
    },
}

# Species focus options for ocean fishing
SPECIES_GENERAL = "general"
SPECIES_SURF_PREDATOR = "surf_predator"
SPECIES_REEF_BREAM = "reef_bream"
SPECIES_PELAGIC = "pelagic"
SPECIES_NIGHT_EEL = "night_eel"

SPECIES_FOCUS = {
    SPECIES_GENERAL: {
        "name": "General Shore Fishing",
        "description": "Mixed species, general conditions",
        "best_tide": "moving",  # rising or falling
        "light_preference": "dawn_dusk",
        "cloud_bonus": 0.5,
        "wave_preference": "moderate",
        "active_months": list(range(1, 13)),  # All year
    },
    SPECIES_SURF_PREDATOR: {
        "name": "Surf Predators (Seabass/Bluefish)",
        "description": "Predators hunting in surf zone",
        "best_tide": "rising",
        "light_preference": "low_light",
        "cloud_bonus": 1.0,
        "wave_preference": "active",  # Bonus for waves
        "wave_bonus": True,
        "active_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],  # Spring-Fall
    },
    SPECIES_REEF_BREAM: {
        "name": "Bream/Reef Species",
        "description": "Bottom feeders around structure",
        "best_tide": "slack_high",
        "light_preference": "day",
        "cloud_bonus": 0.0,
        "wave_preference": "calm",
        "active_months": [5, 6, 7, 8, 9, 10],  # Summer
    },
    SPECIES_PELAGIC: {
        "name": "Pelagic (Mackerel/Bonito)",
        "description": "Open water hunters",
        "best_tide": "any",
        "light_preference": "dawn",
        "cloud_bonus": 0.3,
        "wave_preference": "moderate",
        "active_months": [5, 6, 7, 8, 9, 10, 11],  # Late spring-fall
    },
    SPECIES_NIGHT_EEL: {
        "name": "Night Eel/Conger",
        "description": "Nocturnal bottom feeders",
        "best_tide": "slack",
        "light_preference": "night",
        "cloud_bonus": 0.0,
        "wave_preference": "calm",
        "active_months": list(range(1, 13)),  # All year
    },
}

# Default thresholds for ocean mode
DEFAULT_OCEAN_THRESHOLDS = {
    "max_wind_speed": 25,  # km/h
    "max_gust_speed": 40,  # km/h
    "max_wave_height": 2.0,  # meters
    "max_rain_chance": 70,  # percent
    "min_rain_chance_medium": 30,  # percent
    "min_rain_chance_high": 60,  # percent
    "good_score": 6,  # Threshold for "good" fishing
    "great_score": 8,  # Threshold for "great" fishing
}

# Tide state constants
TIDE_STATE_RISING = "rising"
TIDE_STATE_FALLING = "falling"
TIDE_STATE_SLACK_HIGH = "slack_high"
TIDE_STATE_SLACK_LOW = "slack_low"

# Light condition constants
LIGHT_DAWN = "dawn"
LIGHT_DAY = "day"
LIGHT_DUSK = "dusk"
LIGHT_NIGHT = "night"

# Time period definitions
TIME_PERIOD_DEFINITIONS = {
    TIME_PERIODS_FULL_DAY: {
        "name": "Full Day (4 periods)",
        "description": "Monitor all day: Morning, Afternoon, Evening, Night",
        "periods": [
            {"name": "morning", "start_hour": 6, "end_hour": 12},
            {"name": "afternoon", "start_hour": 12, "end_hour": 18},
            {"name": "evening", "start_hour": 18, "end_hour": 24},
            {"name": "night", "start_hour": 0, "end_hour": 6},
        ],
    },
    TIME_PERIODS_DAWN_DUSK: {
        "name": "Dawn & Dusk Only",
        "description": "Prime fishing times: 1hr before/after sunrise and sunset",
        "periods": [
            {"name": "dawn", "relative_to": "sunrise", "offset_before": 60, "offset_after": 60},
            {"name": "dusk", "relative_to": "sunset", "offset_before": 60, "offset_after": 60},
        ],
    },
}

# Open-Meteo Marine API endpoint
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

# Sensor names for ocean mode
SENSOR_OCEAN_SCORE = "ocean_fishing_score"
SENSOR_TIDE_STATE = "tide_state"
SENSOR_TIDE_STRENGTH = "tide_strength"
SENSOR_WAVE_HEIGHT = "wave_height"
SENSOR_WAVE_PERIOD = "wave_period"