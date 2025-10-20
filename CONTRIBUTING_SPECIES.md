# Contributing Species Profiles

Thank you for contributing to the Fishing Assistant species database! This guide will help you add or modify species profiles.

## Species Profile Structure

Each species profile in `species_profiles.json` follows this structure:

    {
      "id": "unique_species_id",
      "name": "Species Common Name",
      "scientific_name": "Scientific Name",
      "region": "geographic_region",
      "active_months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      "optimal_conditions": {
        "temp_range": [10, 25],
        "wind_max": 15,
        "wave_max": 2.0,
        "tide_preference": ["rising", "high"],
        "time_preference": ["dawn", "dusk", "night"]
      },
      "description": "Brief description of the species and fishing tips"
    }

## Field Descriptions

### Required Fields

- **id**: Unique identifier (lowercase, underscores for spaces)
- **name**: Common name of the species
- **scientific_name**: Scientific/Latin name
- **region**: Geographic region (e.g., "gibraltar", "mediterranean", "atlantic")
- **active_months**: Array of month numbers (1-12) when species is active
- **optimal_conditions**: Object containing ideal fishing conditions

### Optimal Conditions

- **temp_range**: [min, max] water temperature in Celsius
- **wind_max**: Maximum wind speed in m/s for good fishing
- **wave_max**: Maximum wave height in meters
- **tide_preference**: Array of preferred tide states
  - Options: "low", "rising", "high", "falling"
- **time_preference**: Array of preferred times of day
  - Options: "dawn", "day", "dusk", "night"

### Optional Fields

- **description**: Additional information about the species and fishing tips

## Adding a New Species

1. Open `species_profiles.json`
2. Add a new object to the `species` array
3. Fill in all required fields
4. Ensure the `id` is unique
5. Validate the JSON syntax
6. Test the integration in Home Assistant

## Example: Adding a New Species

    {
      "id": "bluefin_tuna",
      "name": "Bluefin Tuna",
      "scientific_name": "Thunnus thynnus",
      "region": "mediterranean",
      "active_months": [5, 6, 7, 8, 9],
      "optimal_conditions": {
        "temp_range": [18, 24],
        "wind_max": 10,
        "wave_max": 1.5,
        "tide_preference": ["rising", "high"],
        "time_preference": ["dawn", "dusk"]
      },
      "description": "Large pelagic species. Best caught during early morning or evening hours with live bait."
    }

## Regional Guidelines

### Gibraltar
- Include species common to the Strait of Gibraltar
- Consider both Atlantic and Mediterranean influences
- Account for strong currents and tidal flows

### Mediterranean
- Focus on species native to Mediterranean waters
- Consider seasonal migrations
- Account for typically calmer conditions

### Atlantic
- Include Atlantic coastal species
- Consider larger wave conditions
- Account for stronger tidal influences

## Best Practices

1. **Research**: Verify species information from reliable sources
2. **Local Knowledge**: Consult with local anglers for accurate data
3. **Seasonal Accuracy**: Ensure active months reflect actual fishing seasons
4. **Realistic Conditions**: Set optimal conditions based on real fishing experience
5. **Testing**: Test your additions in Home Assistant before submitting

## Submitting Your Contribution

1. Fork the repository
2. Create a new branch for your changes
3. Add or modify species profiles
4. Test the integration
5. Submit a pull request with:
   - Clear description of species added/modified
   - Source of information (if applicable)
   - Any testing performed

## Questions?

If you have questions about contributing species profiles, please open an issue on GitHub.

## Data Sources

Recommended sources for species information:
- Local fishing authorities
- Marine biology databases
- Fishing forums and communities
- Scientific publications
- Local fishing guides and experts

Thank you for helping make Fishing Assistant better for everyone!
