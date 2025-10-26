class FishingAssistantCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._expandedDays = new Set();
    this._showDetails = null;
  }

  static getConfigElement() {
    return document.createElement('fishing-assistant-card-editor');
  }

  static getStubConfig() {
    return {
      entity: '',
      show_forecast: true,
      show_current_conditions: true,
      compact_mode: false,
      forecast_days: 5,
      expand_forecast: false,
      show_component_scores: true
    };
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Please define an entity');
    }
    this.config = {
      show_forecast: true,
      show_current_conditions: true,
      compact_mode: false,
      forecast_days: 5,
      expand_forecast: false,
      show_component_scores: true,
      ...config
    };
  }

  set hass(hass) {
    this._hass = hass;
    const entity = hass.states[this.config.entity];

    if (!entity) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div class="card-content">Entity not found: ${this.config.entity}</div>
        </ha-card>
      `;
      return;
    }

    this.render(entity);
  }

  toggleDay(date) {
    if (this._expandedDays.has(date)) {
      this._expandedDays.delete(date);
    } else {
      this._expandedDays.add(date);
    }
    this.render(this._hass.states[this.config.entity]);
  }

  showBlockDetails(event, dayDate, blockName, period) {
    event.stopPropagation();
    const detailsKey = `${dayDate}-${blockName}`;

    if (this._showDetails === detailsKey) {
      this._showDetails = null;
    } else {
      this._showDetails = detailsKey;
    }

    this.updatePopups();
  }

  updatePopups() {
    this.shadowRoot.querySelectorAll('.block-details').forEach(popup => {
      popup.style.display = 'none';
    });

    const backdrop = this.shadowRoot.querySelector('.popup-backdrop');
    if (backdrop) {
      backdrop.classList.remove('active');
    }

    if (this._showDetails) {
      const activePopup = this.shadowRoot.querySelector(`[data-details-key="${this._showDetails}"]`);
      if (activePopup) {
        activePopup.style.display = 'block';
        if (backdrop) {
          backdrop.classList.add('active');
        }
      }
    }
  }

  getMarineDetails(hass, entity) {
    const location = entity.attributes.location_key || entity.attributes.location;
    if (!location) return {};
    
    const locationKey = location.toLowerCase().replace(/ /g, '_');
    if (!locationKey) return {};

    const waveHeightEntity = hass.states[`sensor.${locationKey}_wave_height`];
    const wavePeriodEntity = hass.states[`sensor.${locationKey}_wave_period`];
    const tideStateEntity = hass.states[`sensor.${locationKey}_tide_state`];
    const tideStrengthEntity = hass.states[`sensor.${locationKey}_tide_strength`];
    const windSpeedEntity = hass.states[`sensor.${locationKey}_wind_speed`];
    const windGustEntity = hass.states[`sensor.${locationKey}_wind_gust`];

    return {
      wave_height: waveHeightEntity?.state,
      wave_period: wavePeriodEntity?.state,
      tide_state: tideStateEntity?.state,
      tide_strength: tideStrengthEntity?.state,
      next_high_tide: tideStateEntity?.attributes?.next_high,
      wind_speed: windSpeedEntity?.state,
      wind_gust: windGustEntity?.state,
    };
  }

  render(entity) {
    const attrs = entity.attributes;
    const config = this.config;

    const rawScore = parseFloat(entity.state);
    const score = Math.round(rawScore * 10);

    const marineDetails = this.getMarineDetails(this._hass, entity);

    const getScoreColor = (score) => {
      if (score >= 70) return '#4caf50';
      if (score >= 40) return '#ff9800';
      return '#f44336';
    };

    const getScoreLabel = (score) => {
      if (score >= 70) return 'Excellent';
      if (score >= 40) return 'Good';
      return 'Poor';
    };

    const getTideEmoji = (tide) => {
      const tideMap = {
        'high_tide': '🌊',
        'slack_high': '🌊',
        'low_tide': '🏖️',
        'slack_low': '🏖️',
        'rising': '📈',
        'falling': '📉'
      };
      return tideMap[tide] || '〰️';
    };

    const getSafetyEmoji = (safetyStatus) => {
      if (!safetyStatus) return '❓';
      const status = typeof safetyStatus === 'object' ? safetyStatus.status : safetyStatus;
      const safetyMap = {
        'safe': '✅',
        'caution': '⚠️',
        'unsafe': '🚫'
      };
      return safetyMap[status] || '❓';
    };

    const getSafetyStatus = (safetyData) => {
      if (!safetyData) return 'unknown';
      return typeof safetyData === 'object' ? safetyData.status : safetyData;
    };

    const getSafetyReasons = (safetyData) => {
      if (!safetyData || typeof safetyData !== 'object') return [];
      return safetyData.reasons || [];
    };

    const getHabitatDetails = (habitatPreset) => {
      const habitatMap = {
        'open_beach': {
          name: 'Open Beach',
          icon: '🏖️',
          max_wind: 25,
          max_gust: 40,
          max_wave: 2.0
        },
        'rocky_point': {
          name: 'Rocky Point',
          icon: '🪨',
          max_wind: 30,
          max_gust: 45,
          max_wave: 2.5
        },
        'harbour': {
          name: 'Harbour/Jetty',
          icon: '⚓',
          max_wind: 35,
          max_gust: 50,
          max_wave: 3.0
        },
        'reef': {
          name: 'Reef',
          icon: '🐠',
          max_wind: 20,
          max_gust: 35,
          max_wave: 1.5
        },
        'lake': {
          name: 'Lake',
          icon: '🏞️',
          max_wind: 25,
          max_gust: 40,
          max_wave: 0.5
        },
        'river': {
          name: 'River',
          icon: '🌊',
          max_wind: 30,
          max_gust: 45,
          max_wave: 0.3
        },
        'pond': {
          name: 'Pond',
          icon: '💧',
          max_wind: 35,
          max_gust: 50,
          max_wave: 0.2
        }
      };
      return habitatMap[habitatPreset] || null;
    };

    const scoreColor = getScoreColor(score);
    const scoreLabel = getScoreLabel(score);

    const componentScores = attrs.component_scores || {};
    const breakdown = attrs.breakdown || {};

    const safetyStatus = getSafetyStatus(attrs.safety);
    const safetyReasons = getSafetyReasons(attrs.safety);

    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
          background: var(--card-background-color);
        }
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: ${config.compact_mode ? '12px' : '20px'};
        }
        .title {
          font-size: ${config.compact_mode ? '20px' : '24px'};
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .location {
          font-size: 14px;
          color: var(--secondary-text-color);
        }
        .score-container {
          text-align: center;
          margin-bottom: ${config.compact_mode ? '16px' : '24px'};
        }
        .score-circle {
          width: ${config.compact_mode ? '100px' : '120px'};
          height: ${config.compact_mode ? '100px' : '120px'};
          border-radius: 50%;
          background: ${scoreColor};
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          margin: 0 auto 12px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .score-value {
          font-size: ${config.compact_mode ? '40px' : '48px'};
          font-weight: bold;
          color: white;
          line-height: 1;
        }
        .score-label {
          font-size: 14px;
          color: white;
          opacity: 0.9;
          margin-top: 4px;
        }
        .rating-label {
          text-align: center;
          font-size: 16px;
          color: var(--primary-text-color);
          margin-bottom: 16px;
          font-weight: 500;
        }
        .current-conditions {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
          gap: 12px;
          margin-bottom: 24px;
        }
        .condition-item {
          background: var(--secondary-background-color);
          padding: 12px;
          border-radius: 8px;
          text-align: center;
        }
        .condition-icon {
          font-size: 24px;
          margin-bottom: 4px;
        }
        .condition-label {
          font-size: 11px;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          margin-bottom: 4px;
        }
        .condition-value {
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .safety-warning {
          background: #f44336;
          color: white;
          padding: 12px;
          border-radius: 8px;
          margin-bottom: 16px;
          text-align: center;
          font-weight: 500;
        }
        .safety-caution {
          background: #ff9800;
          color: white;
          padding: 12px;
          border-radius: 8px;
          margin-bottom: 16px;
          text-align: center;
          font-weight: 500;
        }
        .safety-reasons {
          font-size: 12px;
          margin-top: 8px;
          line-height: 1.4;
        }
        .habitat-info {
          background: var(--secondary-background-color);
          padding: 12px;
          border-radius: 8px;
          margin-bottom: 16px;
          border-left: 3px solid var(--primary-color);
        }
        .habitat-header {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 8px;
        }
        .habitat-icon {
          font-size: 20px;
        }
        .habitat-name {
          font-size: 14px;
          font-weight: 600;
          color: var(--primary-text-color);
        }
        .habitat-thresholds {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          font-size: 11px;
        }
        .habitat-threshold {
          text-align: center;
          padding: 4px;
          background: var(--card-background-color);
          border-radius: 4px;
        }
        .threshold-label {
          color: var(--secondary-text-color);
          font-size: 10px;
          text-transform: uppercase;
          margin-bottom: 2px;
        }
        .threshold-value {
          color: var(--primary-text-color);
          font-weight: 600;
        }
        .component-scores {
          margin-bottom: 24px;
        }
        .component-scores-title {
          font-size: 14px;
          font-weight: 500;
          margin-bottom: 12px;
          color: var(--primary-text-color);
        }
        .score-bar-container {
          margin-bottom: 12px;
        }
        .score-bar-header {
          display: flex;
          justify-content: space-between;
          margin-bottom: 4px;
          font-size: 12px;
        }
        .score-bar-label {
          color: var(--secondary-text-color);
          text-transform: capitalize;
        }
        .score-bar-value {
          color: var(--primary-text-color);
          font-weight: 500;
        }
        .score-bar-track {
          height: 8px;
          background: var(--divider-color);
          border-radius: 4px;
          overflow: hidden;
        }
        .score-bar-fill {
          height: 100%;
          background: linear-gradient(90deg, #f44336 0%, #ff9800 50%, #4caf50 100%);
          transition: width 0.3s;
        }
        .forecast-section {
          margin-top: 24px;
        }
        .forecast-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
          cursor: pointer;
          user-select: none;
        }
        .forecast-title {
          font-size: 18px;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .forecast-toggle {
          font-size: 12px;
          color: var(--secondary-text-color);
          padding: 4px 8px;
          background: var(--secondary-background-color);
          border-radius: 4px;
        }
        .forecast-day {
          margin-bottom: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          overflow: hidden;
        }
        .day-header {
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
          padding: 12px;
          background: var(--secondary-background-color);
          display: flex;
          justify-content: space-between;
          align-items: center;
          cursor: pointer;
          user-select: none;
        }
        .day-header:hover {
          background: var(--divider-color);
        }
        .day-info {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .day-avg {
          font-size: 12px;
          color: var(--secondary-text-color);
          font-weight: normal;
        }
        .expand-icon {
          transition: transform 0.3s;
        }
        .expand-icon.expanded {
          transform: rotate(180deg);
        }
        .time-blocks {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 8px;
          padding: 12px;
          background: var(--card-background-color);
          position: relative;
        }
        .time-blocks.collapsed {
          display: none;
        }
        .time-block {
          background: var(--secondary-background-color);
          padding: 8px;
          border-radius: 6px;
          text-align: center;
          border-left: 3px solid transparent;
          cursor: pointer;
          transition: transform 0.2s, box-shadow 0.2s;
          position: relative;
        }
        .time-block:hover {
          transform: translateY(-2px);
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .time-block.excellent {
          border-left-color: #4caf50;
        }
        .time-block.good {
          border-left-color: #ff9800;
        }
        .time-block.poor {
          border-left-color: #f44336;
        }
        .time-block.unsafe {
          background: rgba(244, 67, 54, 0.1);
        }
        .time-block.caution {
          background: rgba(255, 152, 0, 0.1);
        }
        .block-time {
          font-size: 10px;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          margin-bottom: 4px;
        }
        .block-score {
          font-size: 18px;
          font-weight: bold;
          color: var(--primary-text-color);
        }
        .block-conditions {
          font-size: 10px;
          margin-top: 4px;
          display: flex;
          justify-content: center;
          gap: 4px;
          flex-wrap: wrap;
        }
        .block-tide {
          font-size: 12px;
          margin-top: 4px;
        }
        .block-safety {
          font-size: 10px;
          font-weight: 500;
          margin-top: 2px;
        }
        .block-details {
          position: fixed;
          left: 50%;
          top: 50%;
          transform: translate(-50%, -50%);
          padding: 16px;
          background: var(--card-background-color);
          border-radius: 8px;
          border: 2px solid var(--primary-color);
          box-shadow: 0 8px 24px rgba(0,0,0,0.3);
          font-size: 12px;
          text-align: left;
          z-index: 9999;
          min-width: 280px;
          max-width: 90vw;
          max-height: 80vh;
          overflow-y: auto;
          display: none;
        }
        .block-details.active {
          display: block;
        }
        .popup-backdrop {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.5);
          z-index: 9998;
          display: none;
        }
        .popup-backdrop.active {
          display: block;
        }
        .detail-section {
          margin-bottom: 12px;
        }
        .detail-section:last-child {
          margin-bottom: 0;
        }
        .detail-section-title {
          font-size: 11px;
          font-weight: 600;
          color: var(--primary-color);
          text-transform: uppercase;
          margin-bottom: 6px;
          border-bottom: 1px solid var(--divider-color);
          padding-bottom: 2px;
        }
        .detail-row {
          display: flex;
          justify-content: space-between;
          padding: 3px 0;
        }
        .detail-label {
          color: var(--secondary-text-color);
          font-size: 11px;
        }
        .detail-value {
          color: var(--primary-text-color);
          font-weight: 500;
          font-size: 11px;
        }
        .detail-warning {
          color: #f44336;
          font-weight: 600;
          background: rgba(244, 67, 54, 0.1);
          padding: 6px;
          border-radius: 4px;
          margin-top: 6px;
          text-align: center;
          font-size: 11px;
          line-height: 1.4;
        }
        .detail-caution {
          color: #ff9800;
          font-weight: 600;
          background: rgba(255, 152, 0, 0.1);
          padding: 6px;
          border-radius: 4px;
          margin-top: 6px;
          text-align: center;
          font-size: 11px;
          line-height: 1.4;
        }
        .detail-good {
          color: #4caf50;
          font-weight: 600;
          background: rgba(76, 175, 80, 0.1);
          padding: 6px;
          border-radius: 4px;
          margin-top: 6px;
          text-align: center;
          font-size: 11px;
        }
        .close-hint {
          margin-top: 8px;
          font-size: 10px;
          color: var(--secondary-text-color);
          text-align: center;
          font-style: italic;
        }
        @media (max-width: 600px) {
          .time-blocks {
            grid-template-columns: repeat(2, 1fr);
          }
        }
      </style>

      <ha-card>
        <div class="popup-backdrop ${this._showDetails ? 'active' : ''}"></div>

        <div class="header">
          <div>
            <div class="title">🎣 Fishing Assistant</div>
            ${attrs.location ? `<div class="location">${attrs.location}</div>` : ''}
          </div>
        </div>

        ${safetyStatus === 'unsafe' ? `
          <div class="safety-warning">
            🚫 Unsafe Conditions - Not Recommended
            ${safetyReasons.length > 0 ? `<div class="safety-reasons">${safetyReasons.join('<br>')}</div>` : ''}
          </div>
        ` : safetyStatus === 'caution' ? `
          <div class="safety-caution">
            ⚠️ Caution - Check Conditions Carefully
            ${safetyReasons.length > 0 ? `<div class="safety-reasons">${safetyReasons.join('<br>')}</div>` : ''}
          </div>
        ` : ''}

        <div class="score-container">
          <div class="score-circle">
            <div class="score-value">${score}</div>
            <div class="score-label">${scoreLabel}</div>
          </div>
        </div>

        ${attrs.rating ? `
          <div class="rating-label">${attrs.rating}</div>
        ` : ''}

        ${(() => {
          const habitatDetails = getHabitatDetails(attrs.habitat);
          if (!habitatDetails) return '';
          return `
            <div class="habitat-info">
              <div class="habitat-header">
                <span class="habitat-icon">${habitatDetails.icon}</span>
                <span class="habitat-name">${habitatDetails.name}</span>
              </div>
              <div class="habitat-thresholds">
                <div class="habitat-threshold">
                  <div class="threshold-label">Max Wind</div>
                  <div class="threshold-value">${habitatDetails.max_wind} km/h</div>
                </div>
                <div class="habitat-threshold">
                  <div class="threshold-label">Max Gust</div>
                  <div class="threshold-value">${habitatDetails.max_gust} km/h</div>
                </div>
                <div class="habitat-threshold">
                  <div class="threshold-label">Max Wave</div>
                  <div class="threshold-value">${habitatDetails.max_wave}m</div>
                </div>
              </div>
            </div>
          `;
        })()}

        ${config.show_current_conditions ? `
          <div class="current-conditions">
            ${attrs.species_focus && attrs.species_focus !== 'Unknown' ? `
              <div class="condition-item">
                <div class="condition-icon">🐟</div>
                <div class="condition-label">Species</div>
                <div class="condition-value">${attrs.species_focus}</div>
              </div>
            ` : ''}

            ${attrs.fish ? `
              <div class="condition-item">
                <div class="condition-icon">🐟</div>
                <div class="condition-label">Species</div>
                <div class="condition-value">${attrs.fish}</div>
              </div>
            ` : ''}

            ${attrs.tide_state ? `
              <div class="condition-item">
                <div class="condition-icon">${getTideEmoji(attrs.tide_state)}</div>
                <div class="condition-label">Tide</div>
                <div class="condition-value">${attrs.tide_state.replace(/_/g, ' ')}</div>
              </div>
            ` : ''}

            ${safetyStatus ? `
              <div class="condition-item">
                <div class="condition-icon">${getSafetyEmoji(attrs.safety)}</div>
                <div class="condition-label">Safety</div>
                <div class="condition-value">${safetyStatus}</div>
              </div>
            ` : ''}

            ${marineDetails?.wave_height ? `
              <div class="condition-item">
                <div class="condition-icon">🌊</div>
                <div class="condition-label">Waves</div>
                <div class="condition-value">${parseFloat(marineDetails.wave_height).toFixed(1)}m</div>
              </div>
            ` : ''}

            ${marineDetails?.wind_speed ? `
              <div class="condition-item">
                <div class="condition-icon">💨</div>
                <div class="condition-label">Wind Speed</div>
                <div class="condition-value">${Math.round(marineDetails.wind_speed)} km/h</div>
              </div>
            ` : ''}

            ${marineDetails?.next_high_tide ? `
              <div class="condition-item">
                <div class="condition-icon">⏰</div>
                <div class="condition-label">Next High Tide</div>
                <div class="condition-value">${new Date(marineDetails.next_high_tide).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
              </div>
            ` : ''}
          </div>
        ` : ''}

        ${config.show_component_scores && Object.keys(componentScores).length > 0 ? `
          <div class="component-scores">
            <div class="component-scores-title">📊 Score Breakdown</div>
            ${Object.entries(componentScores).map(([key, value]) => {
              const percentage = Math.round(value * 100);
              return `
                <div class="score-bar-container">
                  <div class="score-bar-header">
                    <span class="score-bar-label">${key.replace(/_/g, ' ')}</span>
                    <span class="score-bar-value">${percentage}%</span>
                  </div>
                  <div class="score-bar-track">
                    <div class="score-bar-fill" style="width: ${percentage}%"></div>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        ` : ''}

        ${config.show_forecast && attrs.forecast && attrs.forecast.length > 0 ? `
          <div class="forecast-section">
            <div class="forecast-header" onclick="this.getRootNode().host.toggleAllDays()">
              <div class="forecast-title">📅 Forecast</div>
              <div class="forecast-toggle">
                ${this._expandedDays.size > 0 ? 'Collapse All' : 'Expand All'}
              </div>
            </div>
            ${this.renderForecast(attrs.forecast, config.forecast_days)}
          </div>
        ` : ''}
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll('.day-header').forEach(header => {
      header.addEventListener('click', (e) => {
        const date = e.currentTarget.dataset.date;
        this.toggleDay(date);
      });
    });

    this.shadowRoot.querySelectorAll('.time-block').forEach(block => {
      block.addEventListener('click', (e) => {
        const dayDate = e.currentTarget.dataset.day;
        const blockName = e.currentTarget.dataset.block;
        const periodData = JSON.parse(e.currentTarget.dataset.period);
        this.showBlockDetails(e, dayDate, blockName, periodData);
      });
    });

    const backdrop = this.shadowRoot.querySelector('.popup-backdrop');
    if (backdrop) {
      backdrop.addEventListener('click', () => {
        this._showDetails = null;
        this.updatePopups();
      });
    }

    this.updatePopups();
  }

  toggleAllDays() {
    const entity = this._hass.states[this.config.entity];
    const forecast = entity.attributes.forecast;

    if (!forecast || forecast.length === 0) return;

    if (this._expandedDays.size > 0) {
      this._expandedDays.clear();
    } else {
      if (Array.isArray(forecast)) {
        forecast.forEach((day, index) => this._expandedDays.add(day.date || index.toString()));
      } else {
        Object.keys(forecast).forEach(date => this._expandedDays.add(date));
      }
    }

    this.render(entity);
  }

  renderForecast(forecast, maxDays = 5) {
    if (!forecast || forecast.length === 0) return '';

    const forecastArray = Array.isArray(forecast) ? forecast.slice(0, maxDays) : [];

    const getTideEmoji = (tide) => {
      if (!tide) return '〰️';
      const tideMap = {
        'high_tide': '🌊',
        'slack_high': '🌊',
        'low_tide': '🏖️',
        'slack_low': '🏖️',
        'rising': '📈',
        'falling': '📉'
      };
      return tideMap[tide] || '〰️';
    };

    const getScoreClass = (score) => {
      const scoreValue = Math.round(score * 100);
      if (scoreValue >= 70) return 'excellent';
      if (scoreValue >= 40) return 'good';
      return 'poor';
    };

    const getSafetyClass = (safety) => {
      const status = typeof safety === 'object' ? safety.status : safety;
      if (status === 'unsafe') return 'unsafe';
      if (status === 'caution') return 'caution';
      return '';
    };

    return forecastArray.map((day, index) => {
      const dayDate = day.date || day.datetime || index.toString();
      const dayName = day.day_name || new Date(dayDate).toLocaleDateString('en-US', { weekday: 'short' });
      const avgScore = Math.round((day.score || 0) * 100);
      const isExpanded = this._expandedDays.has(dayDate) || this.config.expand_forecast;

      const rating = day.rating || '';
      const safetyStatus = typeof day.safety === 'object' ? day.safety.status : day.safety;
      const safetyReasons = typeof day.safety === 'object' ? day.safety.reasons : [];

      // Get periods data
      const periods = day.periods || {};
      const periodOrder = ['morning', 'afternoon', 'evening', 'night'];

      return `
        <div class="forecast-day">
          <div class="day-header" data-date="${dayDate}">
            <div class="day-info">
              <span>${dayName}</span>
              <span class="day-avg">Score: ${avgScore} ${rating ? `(${rating})` : ''}</span>
            </div>
            <span class="expand-icon ${isExpanded ? 'expanded' : ''}">▼</span>
          </div>
          <div class="time-blocks ${isExpanded ? '' : 'collapsed'}">
            ${periodOrder.map(periodName => {
              const period = periods[periodName];
              if (!period) return '';

              const periodScore = Math.round((period.score || 0) * 100);
              const scoreClass = getScoreClass(period.score || 0);
              const safetyClass = getSafetyClass(period.safety);
              const periodSafetyStatus = typeof period.safety === 'object' ? period.safety.status : period.safety;
              
              const periodDataJson = JSON.stringify(period).replace(/"/g, '&quot;');

              return `
                <div class="time-block ${scoreClass} ${safetyClass}" 
                     data-day="${dayDate}" 
                     data-block="${periodName}" 
                     data-period="${periodDataJson}">
                  <div class="block-time">${periodName}</div>
                  <div class="block-score">${periodScore}</div>
                  ${period.tide_state ? `
                    <div class="block-tide">${getTideEmoji(period.tide_state)}</div>
                  ` : ''}
                  ${periodSafetyStatus && periodSafetyStatus !== 'safe' ? `
                    <div class="block-safety" style="color: ${periodSafetyStatus === 'unsafe' ? '#f44336' : '#ff9800'};">
                      ${periodSafetyStatus === 'unsafe' ? '🚫' : '⚠️'}
                    </div>
                  ` : ''}
                </div>
                <div class="block-details" data-details-key="${dayDate}-${periodName}">
                  <div class="detail-section">
                    <div class="detail-section-title">${periodName.toUpperCase()} - ${dayName}</div>
                    <div class="detail-row">
                      <span class="detail-label">Score</span>
                      <span class="detail-value">${periodScore}/100</span>
                    </div>
                    ${period.rating ? `
                      <div class="detail-row">
                        <span class="detail-label">Rating</span>
                        <span class="detail-value">${period.rating}</span>
                      </div>
                    ` : ''}
                  </div>

                  ${period.conditions ? `
                    <div class="detail-section">
                      <div class="detail-section-title">Conditions</div>
                      ${period.conditions.temperature !== undefined ? `
                        <div class="detail-row">
                          <span class="detail-label">Temperature</span>
                          <span class="detail-value">${Math.round(period.conditions.temperature)}°C</span>
                        </div>
                      ` : ''}
                      ${period.conditions.wind_speed !== undefined ? `
                        <div class="detail-row">
                          <span class="detail-label">Wind Speed</span>
                          <span class="detail-value">${Math.round(period.conditions.wind_speed)} km/h</span>
                        </div>
                      ` : ''}
                      ${period.conditions.wind_gust !== undefined ? `
                        <div class="detail-row">
                          <span class="detail-label">Wind Gust</span>
                          <span class="detail-value">${Math.round(period.conditions.wind_gust)} km/h</span>
                        </div>
                      ` : ''}
                      ${period.conditions.wave_height !== undefined ? `
                        <div class="detail-row">
                          <span class="detail-label">Wave Height</span>
                          <span class="detail-value">${period.conditions.wave_height.toFixed(1)}m</span>
                        </div>
                      ` : ''}
                      ${period.tide_state ? `
                        <div class="detail-row">
                          <span class="detail-label">Tide</span>
                          <span class="detail-value">${getTideEmoji(period.tide_state)} ${period.tide_state.replace(/_/g, ' ')}</span>
                        </div>
                      ` : ''}
                    </div>
                  ` : ''}

                  ${period.component_scores && Object.keys(period.component_scores).length > 0 ? `
                    <div class="detail-section">
                      <div class="detail-section-title">Component Scores</div>
                      ${Object.entries(period.component_scores).map(([key, value]) => `
                        <div class="detail-row">
                          <span class="detail-label">${key.replace(/_/g, ' ')}</span>
                          <span class="detail-value">${Math.round(value * 100)}%</span>
                        </div>
                      `).join('')}
                    </div>
                  ` : ''}

                  ${periodSafetyStatus ? `
                    <div class="${periodSafetyStatus === 'unsafe' ? 'detail-warning' : periodSafetyStatus === 'caution' ? 'detail-caution' : 'detail-good'}">
                      ${periodSafetyStatus === 'unsafe' ? '🚫 Unsafe' : periodSafetyStatus === 'caution' ? '⚠️ Caution' : '✅ Safe'}
                      ${typeof period.safety === 'object' && period.safety.reasons && period.safety.reasons.length > 0 ? `
                        <div style="margin-top: 4px;">${period.safety.reasons.join(' • ')}</div>
                      ` : ''}
                    </div>
                  ` : ''}

                  <div class="close-hint">Click outside to close</div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    }).join('');
  }

  getCardSize() {
    return this.config.compact_mode ? 4 : 6;
  }
}

class FishingAssistantCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
  }

  setConfig(config) {
    this._config = {
      show_forecast: true,
      show_current_conditions: true,
      compact_mode: false,
      forecast_days: 5,
      expand_forecast: false,
      show_component_scores: true,
      ...config
    };
    if (!this.rendered) {
      this.render();
      this.rendered = true;
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.rendered && this._config) {
      this.render();
      this.rendered = true;
    }
  }

  configChanged(newConfig) {
    const event = new Event('config-changed', {
      bubbles: true,
      composed: true,
    });
    event.detail = { config: newConfig };
    this.dispatchEvent(event);
  }

  render() {
    if (!this._hass) {
      return;
    }

    const entities = Object.keys(this._hass.states)
      .filter(eid => eid.startsWith('sensor.') &&
              (eid.includes('fishing') ||
               this._hass.states[eid].attributes.species_focus ||
               this._hass.states[eid].attributes.fish))
      .sort();

    this.innerHTML = `
      <style>
        .config-section {
          padding: 16px;
          border-bottom: 1px solid var(--divider-color);
        }
        .config-section:last-child {
          border-bottom: none;
        }
        .section-title {
          font-size: 16px;
          font-weight: 500;
          margin-bottom: 12px;
          color: var(--primary-text-color);
        }
        .config-row {
          display: flex;
          flex-direction: column;
          margin-bottom: 16px;
        }
        .config-row:last-child {
          margin-bottom: 0;
        }
        label {
          font-weight: 500;
          margin-bottom: 8px;
          color: var(--primary-text-color);
          font-size: 14px;
        }
        select, input[type="number"] {
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          font-size: 14px;
        }
        .hint {
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-top: 4px;
        }
        .checkbox-row {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 12px;
        }
        .checkbox-row input[type="checkbox"] {
          width: 18px;
          height: 18px;
          cursor: pointer;
        }
        .checkbox-row label {
          margin: 0;
          cursor: pointer;
          font-weight: normal;
        }
      </style>

      <div class="config-section">
        <div class="section-title">Entity</div>
        <div class="config-row">
          <label for="entity-select">Fishing Score Entity (Required)</label>
          <select id="entity-select">
            <option value="">-- Select Entity --</option>
            ${entities.map(eid => `
              <option value="${eid}" ${this._config.entity === eid ? 'selected' : ''}>
                ${this._hass.states[eid].attributes.friendly_name || eid}
              </option>
            `).join('')}
          </select>
          <div class="hint">Select the fishing score sensor entity to display</div>
        </div>
      </div>

      <div class="config-section">
        <div class="section-title">Display Options</div>

        <div class="checkbox-row">
          <input type="checkbox" id="show-current" ${this._config.show_current_conditions ? 'checked' : ''}>
          <label for="show-current">Show Current Conditions</label>
        </div>

        <div class="checkbox-row">
          <input type="checkbox" id="show-component" ${this._config.show_component_scores ? 'checked' : ''}>
          <label for="show-component">Show Component Score Breakdown</label>
        </div>

        <div class="checkbox-row">
          <input type="checkbox" id="show-forecast" ${this._config.show_forecast ? 'checked' : ''}>
          <label for="show-forecast">Show Forecast</label>
        </div>

        <div class="checkbox-row">
          <input type="checkbox" id="compact-mode" ${this._config.compact_mode ? 'checked' : ''}>
          <label for="compact-mode">Compact Mode</label>
        </div>

        <div class="checkbox-row">
          <input type="checkbox" id="expand-forecast" ${this._config.expand_forecast ? 'checked' : ''}>
          <label for="expand-forecast">Expand Forecast by Default</label>
        </div>
      </div>

      <div class="config-section">
        <div class="section-title">Forecast Settings</div>
        <div class="config-row">
          <label for="forecast-days">Number of Forecast Days</label>
          <input type="number" id="forecast-days" min="1" max="7" value="${this._config.forecast_days || 5}">
          <div class="hint">Show 1-7 days of forecast (default: 5)</div>
        </div>
      </div>
    `;

    const select = this.querySelector('#entity-select');
    select.addEventListener('change', (ev) => {
      this._config = { ...this._config, entity: ev.target.value };
      this.configChanged(this._config);
    });

    const showCurrent = this.querySelector('#show-current');
    showCurrent.addEventListener('change', (ev) => {
      this._config = { ...this._config, show_current_conditions: ev.target.checked };
      this.configChanged(this._config);
    });

    const showComponent = this.querySelector('#show-component');
    showComponent.addEventListener('change', (ev) => {
      this._config = { ...this._config, show_component_scores: ev.target.checked };
      this.configChanged(this._config);
    });

    const showForecast = this.querySelector('#show-forecast');
    showForecast.addEventListener('change', (ev) => {
      this._config = { ...this._config, show_forecast: ev.target.checked };
      this.configChanged(this._config);
    });

    const compactMode = this.querySelector('#compact-mode');
    compactMode.addEventListener('change', (ev) => {
      this._config = { ...this._config, compact_mode: ev.target.checked };
      this.configChanged(this._config);
    });

    const expandForecast = this.querySelector('#expand-forecast');
    expandForecast.addEventListener('change', (ev) => {
      this._config = { ...this._config, expand_forecast: ev.target.checked };
      this.configChanged(this._config);
    });

    const forecastDays = this.querySelector('#forecast-days');
    forecastDays.addEventListener('change', (ev) => {
      this._config = { ...this._config, forecast_days: parseInt(ev.target.value) };
      this.configChanged(this._config);
    });
  }
}

customElements.define('fishing-assistant-card', FishingAssistantCard);
customElements.define('fishing-assistant-card-editor', FishingAssistantCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'fishing-assistant-card',
  name: 'Fishing Assistant Card',
  description: 'Display fishing conditions and forecast with detailed breakdowns',
  preview: true,
});