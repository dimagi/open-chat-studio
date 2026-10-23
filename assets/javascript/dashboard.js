/**
 * Dashboard Entry Point
 * Combines all dashboard JavaScript modules
 */

import './dashboard/main.js';
import {ChartManager} from './dashboard/charts.js';

document.addEventListener('DOMContentLoaded', () => {
    window.chartManager = new ChartManager();

    const observer = new MutationObserver(() => {
        window.chartManager.updateChartTheme(document.documentElement.getAttribute('data-theme') === 'dark');
    });
    observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
});
