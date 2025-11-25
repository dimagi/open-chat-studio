// put site-wide dependencies here.
// HTMX setup: https://htmx.org/docs/#installing

// Alpine.js setup with utility plugins
import Alpine from 'alpinejs';
import alpineUtils from './alpine-plugins/alpine-utils.js';

Alpine.plugin(alpineUtils);
window.Alpine = Alpine;

// Backward compatibility shim (TODO: Remove after Phase 6)
import './app.js';

// Other site-wide dependencies
import './alertify';
import './tom-select';
import './theme-toggle';
import './tables';
import 'open-chat-studio-widget';

window.onload = Alpine.start;
