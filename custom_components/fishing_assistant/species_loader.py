"""Species profile loader for Fishing Assistant."""
import json
import logging
import os
from typing import Dict, List, Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SpeciesLoader:
    """Load and manage species profiles from JSON."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the species loader."""
        self.hass = hass
        self._profiles = None

    async def async_load_profiles(self):
        """Load species profiles from JSON file asynchronously."""
        try:
            # Get the path to the JSON file
            json_path = os.path.join(
                os.path.dirname(__file__),
                "species_profiles.json"
            )
            
            # Use async file reading
            def _load_json():
                with open(json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            self._profiles = await self.hass.async_add_executor_job(_load_json)
                
            _LOGGER.info("Loaded species profiles version %s", 
                        self._profiles.get("version", "unknown"))
        except Exception as err:
            _LOGGER.error("Failed to load species profiles: %s", err)
            self._profiles = self._get_fallback_profiles()

    def _get_fallback_profiles(self) -> Dict:
        """Return minimal fallback profiles if JSON fails to load."""
        return {
            "version": "2.1.0-fallback",
            "regions": {
                "global": {
                    "id": "global",
                    "name": "Global",
                    "description": "General species",
                    "coordinates": None,
                    "type": "mixed"
                }
            },
            "species": {
                "general_mixed": {
                    "id": "general_mixed",
                    "name": "General Mixed Species",
                    "emoji": "🎣",
                    "regions": ["global"],
                    "type": "ocean",
                    "active_months": list(range(1, 13)),
                    "best_tide": "moving",
                    "light_preference": "dawn_dusk",
                    "cloud_bonus": 0.5,
                    "wave_preference": "moderate",
                    "temp_range": [8, 26],
                }
            }
        }

    def get_species(self, species_id: str) -> Optional[Dict]:
        """Get a specific species profile by ID."""
        if not self._profiles:
            return None

        species_dict = self._profiles.get("species", {})
        if species_id in species_dict:
            profile = species_dict[species_id].copy()
            return profile

        return None

    def get_species_by_region(self, region: str) -> List[Dict]:
        """Get all species available in a specific region."""
        if not self._profiles:
            return []

        species_dict = self._profiles.get("species", {})
        species_list = []
        
        for species_id, species_data in species_dict.items():
            # Check if this species is available in the requested region
            available_regions = species_data.get("regions", [])
            if region in available_regions:
                profile = species_data.copy()
                profile["id"] = species_id
                profile["region"] = region  # Add the current region context
                species_list.append(profile)

        return species_list

    def get_species_by_type(self, species_type: str) -> List[Dict]:
        """Get all species of a specific type (ocean/freshwater)."""
        if not self._profiles:
            return []

        species_dict = self._profiles.get("species", {})
        species_list = []
        
        for species_id, species_data in species_dict.items():
            if species_data.get("type") == species_type:
                profile = species_data.copy()
                profile["id"] = species_id
                species_list.append(profile)

        return species_list

    def get_regions(self) -> List[Dict]:
        """Get list of available regions with metadata."""
        if not self._profiles:
            return [{"id": "global", "name": "Global", "description": "General species", "type": "mixed"}]

        regions = []
        for region_id, region_data in self._profiles.get("regions", {}).items():
            regions.append({
                "id": region_id,
                "name": region_data.get("name", region_id),
                "description": region_data.get("description", ""),
                "coordinates": region_data.get("coordinates"),
                "type": region_data.get("type", "mixed")
            })

        return regions

    def get_regions_by_type(self, region_type: str) -> List[Dict]:
        """Get regions filtered by type (ocean/freshwater/mixed)."""
        all_regions = self.get_regions()
        return [r for r in all_regions if r.get("type") == region_type]

    def get_all_species(self) -> List[Dict]:
        """Get all species from all regions."""
        if not self._profiles:
            return []

        species_dict = self._profiles.get("species", {})
        all_species = []
        
        for species_id, species_data in species_dict.items():
            profile = species_data.copy()
            profile["id"] = species_id
            all_species.append(profile)

        return all_species

    def get_regions_for_species(self, species_id: str) -> List[str]:
        """Get list of regions where a species is available."""
        species = self.get_species(species_id)
        if species:
            return species.get("regions", [])
        return []

    def get_region_info(self, region_id: str) -> Optional[Dict]:
        """Get detailed information about a specific region."""
        if not self._profiles:
            return None

        regions_dict = self._profiles.get("regions", {})
        if region_id in regions_dict:
            return regions_dict[region_id].copy()

        return None

    def get_freshwater_species_list(self) -> List[str]:
        """Get list of freshwater species IDs for backwards compatibility."""
        freshwater_species = self.get_species_by_type("freshwater")
        return [s["id"] for s in freshwater_species]

    def convert_legacy_fish_name(self, legacy_name: str) -> str:
        """Convert legacy fish names to new species IDs."""
        # Mapping of old names to new IDs
        legacy_mapping = {
            "largemouth_bass": "bass",
            "smallmouth_bass": "bass",
            "rainbow_trout": "trout",
            "brown_trout": "trout",
            "brook_trout": "trout",
            "wels_catfish": "catfish",
        }
        
        return legacy_mapping.get(legacy_name, legacy_name)