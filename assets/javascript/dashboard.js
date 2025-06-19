/**
 * Dashboard Entry Point
 * Combines all dashboard JavaScript modules
 */

// Import dashboard CSS
import '../styles/app/dashboard.css';

// Import dashboard JavaScript modules
import './dashboard/main.js';
import './dashboard/charts.js';
import './dashboard/filters.js';

// Export for potential external usage
export { Dashboard } from './dashboard/main.js';
export { ChartManager } from './dashboard/charts.js';
export { FilterManager } from './dashboard/filters.js';