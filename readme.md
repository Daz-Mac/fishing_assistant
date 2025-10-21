[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/docs/setup/custom_repositories)
[![GitHub release](https://img.shields.io/github/v/release/bairnhard/home-assistant-google-aqi?style=for-the-badge)](https://github.com/bairnhard/home-assistant-google-aqi/releases)

# 🎣 Fishing Assistant for Home Assistant

**Fishing Assistant** is a custom integration for [Home Assistant](https://www.home-assistant.io) that predicts optimal fishing times for your favorite **freshwater** and **ocean** fishing spots — based on weather, tides, solunar theory, and environmental factors.

> _"Is today a good day to go fishing?"_

Let Home Assistant tell you. 🐟

---

## 📦 Features

### 🌊 **Dual Mode Support**
- **Freshwater Mode**: Lakes, rivers, ponds, and reservoirs
- **Ocean Mode**: Shore and ocean fishing with tides, currents, and marine conditions

### 🧠 **Smart Scoring System**
- 0–100 scale for easy interpretation
- Species-specific profiles and seasonal patterns
- Real-time condition analysis
- 5-day forecast with 4 time blocks per day (Morning, Afternoon, Evening, Night)

### 🎨 **Beautiful Custom Card**
- Gorgeous visual dashboard card
- Color-coded scores (red/orange/green)
- Current conditions display (tide, moon, species)
- Interactive 5-day forecast grid
- Safety warnings for dangerous conditions
- Responsive design for mobile and desktop

### 🌅 **Comprehensive Data Sources**
- 🌦️ Live weather from Met.no and Open-Meteo
- 🌊 Tidal data and predictions
- 🌊 Marine conditions (wave height, swell, currents)
- 🌒 Moon phase, transit & Solunar periods
- 🌅 Sunrise/sunset & twilight calculations
- 📍 Location-aware (lat/lon or HA zones)

### 🐟 **Species Intelligence**
- 50+ fish species profiles
- Seasonal activity patterns
- Temperature preferences
- Feeding behavior patterns
- Depth and habitat preferences

---

## 🛠️ Installation

### Via HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Fishing Assistant" in HACS
3. Click Install
4. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/fishing_assistant/` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

---

## 🧭 Configuration

### Adding a Fishing Location

**Via UI (Recommended):**

1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for **Fishing Assistant**
4. Choose your fishing mode:
   - **Freshwater**: For lakes, rivers, ponds, reservoirs
   - **Ocean**: For shore and ocean fishing

#### Freshwater Configuration:
- **Name**: e.g., "Ammersee"
- **Location**: Coordinates or Home Assistant Zone
- **Fish Species**: Comma-separated (e.g., "Pike, Zander, Perch")
- **Body Type**: Lake, River, Pond, or Reservoir

#### Ocean Configuration:
- **Name**: e.g., "Gibraltar Shore"
- **Location**: Coordinates or Home Assistant Zone
- **Target Species**: Select from 50+ ocean species
- **Fishing Type**: Shore or Ocean
- **Weather Entity**: Your Met.no weather integration entity
- **Tide Entity**: Your tide sensor entity (optional but recommended)

---

## 🎨 Using the Custom Card

### Step 1: Add the Card Resource (One-Time Setup)

After installing the integration, you need to register the custom card resource:

1. Go to **Settings → Dashboards**
2. Click the **three dots menu** (top right) → **Resources**
3. Click **+ Add Resource**
4. Enter the following:
   - **URL**: `/fishing_assistant_local/fishing-assistant-card.js`
   - **Resource type**: **JavaScript Module**
5. Click **Create**
6. **Hard refresh your browser** (Ctrl+Shift+R or Cmd+Shift+R)

> **Note**: This is a one-time setup. Once added, the resource will persist across restarts.

### Step 2: Add the Card to Your Dashboard

1. Edit your dashboard
2. Click **Add Card**
3. Search for **"Fishing Assistant Card"**
4. Select your fishing score sensor entity from the dropdown
5. Click **Save**

### Manual YAML Configuration:

```yaml
type: custom:fishing-assistant-card
entity: sensor.gibraltar_shore_fishing_score

```

### Card Features:

- **Current Score Circle**: Large, color-coded display (0-100)
- **Current Conditions**: Species focus, tide state, moon phase, solunar period
- **Safety Warnings**: Prominent alerts for unsafe conditions
- **5-Day Forecast**: Grid view with morning/afternoon/evening/night blocks
- **Visual Indicators**: Emojis and colors for quick interpretation
- **Responsive Design**: Looks great on all screen sizes

---

## 🐟 Scoring System

Each time period is given a **score from 0 to 100**, where:

| Score | Meaning |
|-------|---------|
| 0-30 | ❌ Poor conditions. Stay home. |
| 31-50 | 😐 Fair — might catch something. |
| 51-70 | 👍 Good conditions. Worth going! |
| 71-85 | 🔥 Excellent — pack the rods! |
| 86-100 | 🚨 PERFECT! Drop everything and go! |

### Freshwater Factors:
- ✅ **Air temperature** (proxy for water temp)
- 🌥 **Cloud cover**
- 💨 **Wind speed**
- 🌧 **Precipitation**
- 🧭 **Barometric pressure trend**
- 🌅 **Twilight boost** (1h around sunrise/sunset)
- 🌑 **Moon phase**
- 🌗 **Solunar periods** (transit, underfoot, rise/set)
- 🌊 **Water body type** (affects weightings)

### Ocean Factors:
- 🌊 **Tide state** (high, low, rising, falling)
- 🌊 **Wave height and swell**
- 🌊 **Ocean currents**
- 🌡️ **Water temperature**
- 🌅 **Dawn/dusk feeding periods**
- 🌑 **Moon phase and tidal influence**
- 🌗 **Solunar periods**
- 🐟 **Species seasonal patterns**
- ⚠️ **Safety conditions** (wind, waves, visibility)

---

## 🧠 Example Sensor Output

### Freshwater Sensor:

    sensor.ammersee_zander_fishing_score:
      state: 78
      friendly_name: Ammersee (Zander) Fishing Score
      species_focus: Zander
      moon_phase: waxing_gibbous
      solunar_period: major
      best_window: 04:00 – 06:00
      forecast:
        - day: Today
          time_block: Morning
          score: 78
          best_window: 04:00 – 06:00
        - day: Today
          time_block: Afternoon
          score: 65

### Ocean Sensor:

    sensor.gibraltar_shore_fishing_score:
      state: 82
      friendly_name: Gibraltar Shore Fishing Score
      species_focus: European Bass
      tide_state: rising
      moon_phase: full_moon
      solunar_period: major
      safety: safe
      wave_height: 0.8m
      conditions_summary: Excellent conditions
      forecast:
        - day: Today
          time_block: Morning
          score: 82
          tide_state: rising
          safety: safe
          conditions_summary: Excellent - rising tide, calm seas
        - day: Today
          time_block: Afternoon
          score: 75
          tide_state: high_tide
          safety: safe

---

## 🌊 Ocean Fishing Features

### Supported Species (50+):
- **Bass**: European Bass, Striped Bass, Black Sea Bass
- **Mackerel**: Atlantic Mackerel, Spanish Mackerel, King Mackerel
- **Tuna**: Bluefin Tuna, Yellowfin Tuna, Albacore
- **Snapper**: Red Snapper, Mangrove Snapper, Lane Snapper
- **Grouper**: Red Grouper, Gag Grouper, Black Grouper
- **Flatfish**: Flounder, Halibut, Plaice, Sole
- **Sharks**: Various species
- **And many more!**

### Tidal Intelligence:
- Real-time tide state detection
- Rising/falling tide identification
- Optimal tide windows for target species
- Tide-based scoring adjustments

### Marine Conditions:
- Wave height and swell analysis
- Ocean current strength
- Water temperature tracking
- Visibility conditions
- Wind and weather safety checks

### Safety Features:
- Automatic unsafe condition detection
- High wind warnings
- Dangerous wave alerts
- Poor visibility warnings
- Prominent safety indicators in card

---

## 📊 Forecast System

### 5-Day Forecast with 4 Time Blocks:
- **Morning** (06:00-12:00): Dawn feeding, tide conditions
- **Afternoon** (12:00-18:00): Midday patterns
- **Evening** (18:00-00:00): Dusk feeding, optimal tides
- **Night** (00:00-06:00): Nocturnal species, moon influence

### Each Forecast Block Includes:
- Score (0-100)
- Tide state (ocean mode)
- Safety status
- Conditions summary
- Best fishing windows

### Update Frequency:
- Refreshes 4 times per day (00:00, 06:00, 12:00, 18:00)
- Ensures fresh data for planning

---

## 💡 Tips & Best Practices

### Dashboard Setup:
- Use the custom card for the best visual experience
- Create separate cards for multiple fishing spots
- Combine with weather and tide cards for complete overview

### Species Selection:
- Choose species that match your target fish
- Seasonal patterns are automatically considered
- Multiple species can be tracked with separate sensors

### Location Accuracy:
- Use precise coordinates for best results
- Ocean fishing: Closer to shore = more accurate tide data
- Freshwater: Consider local microclimates

### Data Sources:
- **Weather**: Met.no integration (recommended for ocean)
- **Tides**: Any Home Assistant tide sensor
- **Marine Data**: Automatically fetched from Open-Meteo Marine API

---

## 🐛 Troubleshooting

### Card Not Appearing in Card Picker:
1. Make sure you've added the resource (see "Using the Custom Card" section above)
2. Hard refresh your browser (Ctrl+Shift+R or Cmd+Shift+R)
3. Check that the resource URL is correct: /fishing_assistant_local/fishing-assistant-card.js
4. Verify the resource type is set to "JavaScript Module"

### "Species focus" shows "Unknown":
- This is normal if no species is configured
- Ocean mode: Select a target species during setup
- Freshwater mode: Add species in configuration

### Forecast Not Updating:
- Check that weather/tide entities are working
- Verify internet connectivity
- Check Home Assistant logs for errors

---

## 📚 Roadmap

- 🐠 Bait and lure suggestions based on conditions
- 📊 Historical catch log integration
- 🛰 Satellite water temperature data
- 🌐 Multi-language support
- 🎯 Fishing spot recommendations
- 📱 Mobile app notifications for optimal times
- 🗺️ Integration with fishing maps and charts

---

## 🤝 Contributing

We welcome contributions from the fishing community!

### How to Contribute:

1. **Report Issues**: Found a bug? [Open an issue](https://github.com/bairnhard/fishing_assistant/issues)
2. **Add Species**: Know a fish species we're missing? See [CONTRIBUTING_SPECIES.md](CONTRIBUTING_SPECIES.md)
3. **Improve Scoring**: Local knowledge? Submit a PR with scoring improvements
4. **Translations**: Help translate the integration

### Adding New Species:

See [CONTRIBUTING_SPECIES.md](CONTRIBUTING_SPECIES.md) for detailed instructions on adding fish species profiles.

---

## 📝 License

This project is licensed under the [MIT License](LICENSE).

---

## 🙏 Credits

Built with ❤️ by anglers, for anglers.

**Data Sources:**
- [Met.no](https://www.met.no/) - Weather data
- [Open-Meteo](https://open-meteo.com/) - Marine and weather data
- [Skyfield](https://rhodesmill.org/skyfield/) - Astronomical calculations

**Special Thanks:**
- Home Assistant community
- All contributors and testers
- The fishing community for feedback and species data

---

## 📧 Support

- **Issues**: [GitHub Issues](https://github.com/bairnhard/fishing_assistant/issues)
- **Discussions**: [GitHub Discussions](https://github.com/bairnhard/fishing_assistant/discussions)
- **Documentation**: [Wiki](https://github.com/bairnhard/fishing_assistant/wiki)

---

**Tight lines! 🎣**
