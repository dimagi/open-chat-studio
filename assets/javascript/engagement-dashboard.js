import './dashboard/engagement/main.js';
import {EngagementChartManager} from './dashboard/engagement/charts.js';

document.addEventListener('DOMContentLoaded', () => {
    window.engagementChartManager = new EngagementChartManager();
});
