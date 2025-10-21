class FishingAssistantCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  static getConfigElement() {
    return document.createElement('fishing-assistant-card-editor');
  }

  static getStubConfig() {
    return { entity: '' };
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Please define an entity');
    }
    this.config = config;
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

  render(entity) {
    const attrs = entity.attributes;
    
    // Convert score from 0-10 to 0-100 scale
    const rawScore = parseFloat(entity.state);
    const score = Math.round(rawScore * 10);
    
    // Determine score color
    const getScoreColor = (score) => {
      if (score >= 70) return '#4caf50'; // Green
      if (score >= 40) return '#ff9800'; // Orange
      return '#f44336'; // Red
    };

    const getScoreLabel = (score) => {
      if (score >= 70) return 'Excellent';
      if (score >= 40) return 'Good';
      return 'Poor';
    };

    // Get tide emoji
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

    // Get safety emoji
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

    // Get moon score as percentage
    const moonScore = attrs.component_scores?.moon ? Math.round(attrs.component_scores.moon * 100) : null;

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
          margin-bottom: 20px;
        }
        .title {
          font-size: 24px;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .location {
          font-size: 14px;
          color: var(--secondary-text-color);
        }
        .score-container {
          text-align: center;
          margin-bottom: 24px;
        }
        .score-circle {
          width: 120px;
          height: 120px;
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
          font-size: 48px;
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
        .forecast-section {
          margin-top: 24px;
        }
        .forecast-title {
          font-size: 18px;
          font-weight: 500;
          margin-bottom: 16px;
          color: var(--primary-text-color);
        }
        .forecast-day {
          margin-bottom: 16px;
        }
        .day-header {
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
          margin-bottom: 8px;
          padding-left: 4px;
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .day-avg {
          font-size: 12px;
          color: var(--secondary-text-color);
          font-weight: normal;
        }
        .time-blocks {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 8px;
        }
        .time-block {
          background: var(--secondary-background-color);
          padding: 8px;
          border-radius: 6px;
          text-align: center;
          border-left: 3px solid transparent;
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
        .block-tide {
          font-size: 12px;
          margin-top: 4px;
        }
        .block-safety {
          font-size: 10px;
          font-weight: 500;
          margin-top: 2px;
        }
        @media (max-width: 600px) {
          .time-blocks {
            grid-template-columns: repeat(2, 1fr);
          }
        }
      </style>

      <ha-card>
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
          
          ${moonScore !== null ? `
            <div class="condition-item">
              <div class="condition-icon">🌙</div>
              <div class="condition-label">Moon</div>
              <div class="condition-value">${moonScore}%</div>
            </div>
          ` : ''}
        </div>

        ${attrs.forecast ? `
          <div class="forecast-section">
            <div class="forecast-title">📅 5-Day Forecast</div>
            ${this.renderForecast(attrs.forecast)}
          </div>
        ` : ''}
      </ha-card>
    `;
  }

  renderForecast(forecast) {
    // Convert nested forecast object to array
    const days = Object.entries(forecast).map(([date, dayData]) => ({
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
      const periods = Object.entries(day.periods).map(([key, period]) => period);
      const avgScore = Math.round(day.daily_avg_score * 10);
      
      return `
        <div class="forecast-day">
          <div class="day-header">
            <span>${day.day_name}</span>
            <span class="day-avg">Avg: ${avgScore}</span>
          </div>
          <div class="time-blocks">
            ${periods.map(period => {
              const score = Math.round(period.score * 10);
              const scoreClass = score >= 70 ? 'excellent' : score >= 40 ? 'good' : 'poor';
              const safetyClass = period.safety === 'unsafe' ? 'unsafe' : period.safety === 'caution' ? 'caution' : '';
              const safetyColor = period.safety === 'unsafe' ? '#f44336' : period.safety === 'caution' ? '#ff9800' : '#4caf50';
              
              return `
                <div class="time-block ${scoreClass} ${safetyClass}">
                  <div class="block-time">${period.time_block}</div>
                  <div class="block-score">${score}</div>
                  <div class="block-tide">${getTideEmoji(period.tide_state)} ${period.tide_state.replace(/_/g, ' ')}</div>
                  <div class="block-safety" style="color: ${safetyColor};">${period.safety}</div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    }).join('');
  }

  getCardSize() {
    return 6;
  }
}

// Visual Editor Component
class FishingAssistantCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
  }

  setConfig(config) {
    this._config = { ...config };
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

    // Get all fishing assistant sensor entities
    const entities = Object.keys(this._hass.states)
      .filter(eid => eid.startsWith('sensor.') && 
              (eid.includes('fishing') || 
               this._hass.states[eid].attributes.species_focus))
      .sort();

    this.innerHTML = `
      <style>
        .config-row {
          display: flex;
          flex-direction: column;
          padding: 16px;
        }
        label {
          font-weight: 500;
          margin-bottom: 8px;
          color: var(--primary-text-color);
        }
        select {
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
      </style>
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
    `;

    const select = this.querySelector('#entity-select');
    select.addEventListener('change', (ev) => {
      this._config = { ...this._config, entity: ev.target.value };
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
  description: 'Display fishing conditions and forecast',
  preview: true,
});
