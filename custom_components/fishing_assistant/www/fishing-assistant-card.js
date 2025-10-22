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

  getWeatherDetails(hass, weatherEntityId) {
    if (!weatherEntityId) return null;
    const weatherState = hass.states[weatherEntityId];
    if (!weatherState) return null;
    
    return {
      wind_speed: weatherState.attributes.wind_speed,
      wind_gust: weatherState.attributes.wind_gust_speed || weatherState.attributes.wind_speed,
      pressure: weatherState.attributes.pressure,
      cloud_cover: weatherState.attributes.cloud_coverage,
      precipitation: weatherState.attributes.precipitation_probability,
      temperature: weatherState.attributes.temperature,
    };
  }

  getMarineDetails(hass, entity) {
    // Use location_key if available, otherwise fall back to location name
    const locationKey = entity.attributes.location_key || entity.attributes.location?.toLowerCase().replace(' ', '_');
    
    if (!locationKey) return {};
    
    const waveHeightEntity = hass.states[`sensor.${locationKey}_wave_height`];
    const wavePeriodEntity = hass.states[`sensor.${locationKey}_wave_period`];
    const tideStateEntity = hass.states[`sensor.${locationKey}_tide_state`];
    const tideStrengthEntity = hass.states[`sensor.${locationKey}_tide_strength`];
    
    return {
      wave_height: waveHeightEntity?.state,
      wave_period: wavePeriodEntity?.state,
      tide_state: tideStateEntity?.state,
      tide_strength: tideStrengthEntity?.state,
    };
  }

  render(entity) {
    const attrs = entity.attributes;
    const config = this.config;
    
    const rawScore = parseFloat(entity.state);
    const score = Math.round(rawScore * 10);
    
    const weatherEntityId = this.findWeatherEntity();
    const weatherDetails = this.getWeatherDetails(this._hass, weatherEntityId);
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

    const getSafetyEmoji = (safety) => {
      const safetyMap = {
        'safe': '✅',
        'caution': '⚠️',
        'unsafe': '🚫'
      };
      return safetyMap[safety] || '❓';
    };

    const scoreColor = getScoreColor(score);
    const scoreLabel = getScoreLabel(score);

    const componentScores = attrs.breakdown?.component_scores || {};
    const weights = attrs.breakdown?.weights || {};

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
        .conditions-summary {
          text-align: center;
          font-size: 14px;
          color: var(--secondary-text-color);
          margin-bottom: 16px;
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
        .best-window {
          background: var(--secondary-background-color);
          padding: 12px;
          border-radius: 8px;
          margin-bottom: 16px;
          text-align: center;
        }
        .best-window-label {
          font-size: 11px;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          margin-bottom: 4px;
        }
        .best-window-value {
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
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

        ${attrs.safety === 'unsafe' ? `
          <div class="safety-warning">
            🚫 Unsafe Conditions - Not Recommended
          </div>
        ` : attrs.safety === 'caution' ? `
          <div class="safety-caution">
            ⚠️ Caution - Check Conditions Carefully
          </div>
        ` : ''}

        <div class="score-container">
          <div class="score-circle">
            <div class="score-value">${score}</div>
            <div class="score-label">${scoreLabel}</div>
          </div>
        </div>

        ${attrs.conditions_summary ? `
          <div class="conditions-summary">${attrs.conditions_summary}</div>
        ` : ''}

        ${attrs.best_window ? `
          <div class="best-window">
            <div class="best-window-label">Best Window</div>
            <div class="best-window-value">${attrs.best_window}</div>
          </div>
        ` : ''}

        ${config.show_current_conditions ? `
          <div class="current-conditions">
            ${attrs.species_focus && attrs.species_focus !== 'Unknown' ? `
              <div class="condition-item">
                <div class="condition-icon">🐟</div>
                <div class="condition-label">Species</div>
                <div class="condition-value">${attrs.species_focus}</div>
              </div>
            ` : ''}
            
            ${attrs.tide_state ? `
              <div class="condition-item">
                <div class="condition-icon">${getTideEmoji(attrs.tide_state)}</div>
                <div class="condition-label">Tide</div>
                <div class="condition-value">${attrs.tide_state.replace(/_/g, ' ')}</div>
              </div>
            ` : ''}
            
            ${attrs.safety ? `
              <div class="condition-item">
                <div class="condition-icon">${getSafetyEmoji(attrs.safety)}</div>
                <div class="condition-label">Safety</div>
                <div class="condition-value">${attrs.safety}</div>
              </div>
            ` : ''}
            
            ${weatherDetails?.wind_speed ? `
              <div class="condition-item">
                <div class="condition-icon">💨</div>
                <div class="condition-label">Wind</div>
                <div class="condition-value">${Math.round(weatherDetails.wind_speed)} km/h</div>
              </div>
            ` : ''}
            
            ${marineDetails?.wave_height ? `
              <div class="condition-item">
                <div class="condition-icon">🌊</div>
                <div class="condition-label">Waves</div>
                <div class="condition-value">${parseFloat(marineDetails.wave_height).toFixed(1)}m</div>
              </div>
            ` : ''}
            
            ${weatherDetails?.cloud_cover !== undefined ? `
              <div class="condition-item">
                <div class="condition-icon">${weatherDetails.cloud_cover < 30 ? '☀️' : weatherDetails.cloud_cover < 70 ? '⛅' : '☁️'}</div>
                <div class="condition-label">Cloud Cover</div>
                <div class="condition-value">${weatherDetails.cloud_cover}%</div>
              </div>
            ` : ''}
            
            ${weatherDetails?.pressure ? `
              <div class="condition-item">
                <div class="condition-icon">🌡️</div>
                <div class="condition-label">Pressure</div>
                <div class="condition-value">${Math.round(weatherDetails.pressure)} hPa</div>
              </div>
            ` : ''}
          </div>
        ` : ''}

        ${config.show_component_scores && Object.keys(componentScores).length > 0 ? `
          <div class="component-scores">
            <div class="component-scores-title">📊 Score Breakdown</div>
            ${Object.entries(componentScores).map(([key, value]) => {
              const percentage = Math.round(value * 100);
              const weight = weights[key] ? Math.round(weights[key] * 100) : 0;
              return `
                <div class="score-bar-container">
                  <div class="score-bar-header">
                    <span class="score-bar-label">${key} ${weight > 0 ? `(${weight}%)` : ''}</span>
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

        ${config.show_forecast && attrs.forecast ? `
          <div class="forecast-section">
            <div class="forecast-header" onclick="this.getRootNode().host.toggleAllDays()">
              <div class="forecast-title">📅 ${config.forecast_days}-Day Forecast</div>
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

  findWeatherEntity() {
    const entity = this._hass.states[this.config.entity];
    if (!entity) return null;
    
    const locationKey = entity.attributes.location_key || entity.attributes.location?.toLowerCase().replace(' ', '_');
    if (!locationKey) return null;
    
    const possibleWeatherEntities = Object.keys(this._hass.states).filter(eid => 
      eid.startsWith('weather.') && eid.includes(locationKey)
    );
    
    return possibleWeatherEntities[0] || null;
  }

  toggleAllDays() {
    const entity = this._hass.states[this.config.entity];
    const forecast = entity.attributes.forecast;
    
    if (this._expandedDays.size > 0) {
      this._expandedDays.clear();
    } else {
      Object.keys(forecast).forEach(date => this._expandedDays.add(date));
    }
    
    this.render(entity);
  }

  renderForecast(forecast, maxDays = 5) {
    const days = Object.entries(forecast)
      .slice(0, maxDays)
      .map(([date, dayData]) => ({
        date,
        day_name: dayData.day_name,
        daily_avg_score: dayData.daily_avg_score,
        periods: dayData.periods
      }));

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

    return days.map(day => {
      const periods = Object.entries(day.periods).map(([key, period]) => ({ ...period, key }));
      const avgScore = Math.round(day.daily_avg_score * 10);
      const isExpanded = this._expandedDays.has(day.date) || this.config.expand_forecast;
      
      return `
        <div class="forecast-day">
          <div class="day-header" data-date="${day.date}">
            <div class="day-info">
              <span>${day.day_name}</span>
              <span class="day-avg">Avg: ${avgScore}</span>
            </div>
            <span class="expand-icon ${isExpanded ? 'expanded' : ''}">▼</span>
          </div>
          <div class="time-blocks ${isExpanded ? '' : 'collapsed'}">
            ${periods.map(period => {
              const score = Math.round(period.score * 10);
              const scoreClass = score >= 70 ? 'excellent' : score >= 40 ? 'good' : 'poor';
              const safetyClass = period.safety === 'unsafe' ? 'unsafe' : period.safety === 'caution' ? 'caution' : '';
              const safetyColor = period.safety === 'unsafe' ? '#f44336' : period.safety === 'caution' ? '#ff9800' : '#4caf50';
              const detailsKey = `${day.date}-${period.key}`;
              const safetyReasons = period.safety_reasons || ['Check local conditions'];
              
              // Get period-specific weather and marine data
              const periodWeather = period.weather || {};
              const periodMarine = period.marine || {};
              
              return `
                <div class="time-block ${scoreClass} ${safetyClass}" 
                     data-day="${day.date}" 
                     data-block="${period.key}"
                     data-period='${JSON.stringify(period)}'>
                  <div class="block-time">${period.time_block}</div>
                  <div class="block-score">${score}</div>
                  <div class="block-conditions">
                    ${periodWeather.wind_speed ? `💨${Math.round(periodWeather.wind_speed)}` : ''}
                    ${periodWeather.cloud_cover !== undefined ? 
                      (periodWeather.cloud_cover < 30 ? '☀️' : periodWeather.cloud_cover < 70 ? '⛅' : '☁️') : ''}
                    ${periodMarine.wave_height ? `🌊${parseFloat(periodMarine.wave_height).toFixed(1)}m` : ''}
                  </div>
                  <div class="block-tide">${getTideEmoji(period.tide_state)}</div>
                  <div class="block-safety" style="color: ${safetyColor};">${period.safety}</div>
                </div>
                
                <div class="block-details" data-details-key="${detailsKey}">
                  <div class="detail-section">
                    <div class="detail-section-title">⚡ Conditions</div>
                    <div class="detail-row">
                      <span class="detail-label">Overall:</span>
                      <span class="detail-value">${period.conditions || 'N/A'}</span>
                    </div>
                    <div class="detail-row">
                      <span class="detail-label">Score:</span>
                      <span class="detail-value">${score}/100</span>
                    </div>
                  </div>
                  
                  <div class="detail-section">
                    <div class="detail-section-title">🌊 Marine</div>
                    <div class="detail-row">
                      <span class="detail-label">Tide:</span>
                      <span class="detail-value">${period.tide_state.replace(/_/g, ' ')}</span>
                    </div>
                    ${periodMarine.wave_height ? `
                      <div class="detail-row">
                        <span class="detail-label">Wave Height:</span>
                        <span class="detail-value">${parseFloat(periodMarine.wave_height).toFixed(1)}m</span>
                      </div>
                    ` : ''}
                    ${periodMarine.wave_period ? `
                      <div class="detail-row">
                        <span class="detail-label">Wave Period:</span>
                        <span class="detail-value">${periodMarine.wave_period}s</span>
                      </div>
                    ` : ''}
                    ${periodMarine.tide_strength ? `
                      <div class="detail-row">
                        <span class="detail-label">Tide Strength:</span>
                        <span class="detail-value">${periodMarine.tide_strength}</span>
                      </div>
                    ` : ''}
                  </div>
                  
                  ${Object.keys(periodWeather).length > 0 ? `
                    <div class="detail-section">
                      <div class="detail-section-title">🌤️ Weather</div>
                      ${periodWeather.wind_speed ? `
                        <div class="detail-row">
                          <span class="detail-label">Wind:</span>
                          <span class="detail-value">${Math.round(periodWeather.wind_speed)} km/h</span>
                        </div>
                      ` : ''}
                      ${periodWeather.wind_gust ? `
                        <div class="detail-row">
                          <span class="detail-label">Gusts:</span>
                          <span class="detail-value">${Math.round(periodWeather.wind_gust)} km/h</span>
                        </div>
                      ` : ''}
                      ${periodWeather.pressure ? `
                        <div class="detail-row">
                          <span class="detail-label">Pressure:</span>
                          <span class="detail-value">${Math.round(periodWeather.pressure)} hPa</span>
                        </div>
                      ` : ''}
                      ${periodWeather.cloud_cover !== undefined ? `
                        <div class="detail-row">
                          <span class="detail-label">Cloud Cover:</span>
                          <span class="detail-value">${periodWeather.cloud_cover}%</span>
                        </div>
                      ` : ''}
                      ${periodWeather.temperature ? `
                        <div class="detail-row">
                          <span class="detail-label">Temperature:</span>
                          <span class="detail-value">${Math.round(periodWeather.temperature)}°C</span>
                        </div>
                      ` : ''}
                    </div>
                  ` : ''}
                  
                  ${period.safety === 'unsafe' ? `
                    <div class="detail-warning">
                      ⚠️ UNSAFE CONDITIONS<br>
                      ${safetyReasons.join('<br>')}
                    </div>
                  ` : period.safety === 'caution' ? `
                    <div class="detail-caution">
                      ⚠️ CAUTION<br>
                      ${safetyReasons.join('<br>')}
                    </div>
                  ` : `
                    <div class="detail-good">
                      ✅ ${safetyReasons[0]}
                    </div>
                  `}
                  
                  <div class="close-hint">Click anywhere to close</div>
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
               this._hass.states[eid].attributes.species_focus))
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
          <input type="number" id="forecast-days" min="1" max="5" value="${this._config.forecast_days || 5}">
          <div class="hint">Show 1-5 days of forecast (default: 5)</div>
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