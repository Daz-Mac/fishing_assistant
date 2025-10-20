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
            "version": "1.0.0-fallback",
            "regions": {
                "global": {
                    "name": "Global",
                    "description": "General species",
                    "species": {
                        "general_mixed": {
                            "id": "general_mixed",
                            "name": "General Mixed Species",
                            "emoji": "🎣",
                            "region": "global",
                            "active_months": list(range(1, 13)),
                            "best_tide": "moving",
                            "light_preference": "dawn_dusk",
                            "cloud_bonus": 0.5,
                            "wave_preference": "moderate",
                        }
                    }
                }
            }
        }

    def get_species(self, species_id: str) -> Optional[Dict]:
        """Get a specific species profile by ID."""
        if not self._profiles:
            return None

        # Search through all regions
        for region_data in self._profiles.get("regions", {}).values():
            species_dict = region_data.get("species", {})
            if species_id in species_dict:
                profile = species_dict[species_id].copy()
                profile["id"] = species_id
                return profile

        return None

    def get_species_by_region(self, region: str) -> List[Dict]:
        """Get all species for a specific region."""
        if not self._profiles:
            return []

        region_data = self._profiles.get("regions", {}).get(region, {})
        species_dict = region_data.get("species", {})
        
        species_list = []
        for species_id, species_data in species_dict.items():
            profile = species_data.copy()
            profile["id"] = species_id
            profile["region"] = region
            species_list.append(profile)

        return species_list

    def get_regions(self) -> List[Dict]:
        """Get list of available regions with metadata."""
        if not self._profiles:
            return [{"id": "global", "name": "Global", "description": "General species"}]

        regions = []
        for region_id, region_data in self._profiles.get("regions", {}).items():
            regions.append({
                "id": region_id,
                "name": region_data.get("name", region_id),
                "description": region_data.get("description", "")
            })

        return regions

    def get_all_species(self) -> List[Dict]:
        """Get all species from all regions."""
        if not self._profiles:
            return []

        all_species = []
        for region_data in self._profiles.get("regions", {}).values():
            for species_id, species_data in region_data.get("species", {}).items():
                profile = species_data.copy()
                profile["id"] = species_id
                all_species.append(profile)

        return all_species
