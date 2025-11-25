/**
 * Window shims for inline event handlers
 * This file imports ES modules and exposes them to window for backwards compatibility
 * with inline onclick handlers. New code should use ES module imports directly.
 */

// Import commonly used functions
import { Cookies, copyToClipboard, copyTextToClipboard } from './app.js';
import { setupTagSelects } from './tag-multiselect.js';
import { trendsChart } from './trends.js';
import { barChartWithDates, cumulativeChartWithDates } from './admin-dashboard.js';
import { initJsonEditors, createJsonEditor, destroyAllEditors, initPythonEditors, createPythonEditor, initPromptEditors, createPromptEditor, createDiffView } from './editors.js';
import { countGPTTokens } from './tiktoken.js';
import { renderPipeline } from './apps/pipeline.tsx';

// Create SiteJS global for backwards compatibility
window.SiteJS = window.SiteJS || {};

window.SiteJS.app = {
  Cookies,
  copyToClipboard,
  copyTextToClipboard
};

window.SiteJS.tagMultiselect = {
  setupTagSelects
};

window.SiteJS.trends = {
  trendsChart
};

window.SiteJS.adminDashboard = {
  barChartWithDates,
  cumulativeChartWithDates
};

window.SiteJS.editors = {
  initJsonEditors,
  createJsonEditor,
  destroyAllEditors,
  initPythonEditors,
  createPythonEditor,
  initPromptEditors,
  createPromptEditor,
  createDiffView
};

window.SiteJS.tokenCounter = {
  countGPTTokens
};

window.SiteJS.pipeline = {
  renderPipeline
};
