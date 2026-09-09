// put site-wide dependencies here.
// HTMX setup: https://htmx.org/docs/#installing
import './alpine';
import './alertify';
import './tom-select';
import './theme-toggle';
import './tables'
import 'open-chat-studio-widget';

// Registers `hx-ext="morph"` against the page's real htmx instance. `{% htmx_script %}`
// loads that instance with `defer`, so it isn't defined yet when this (non-deferred) bundle
// runs -- wait for DOMContentLoaded, which always fires after deferred scripts finish.
// Importing the classic build rather than "idiomorph/htmx" also matters: the latter
// re-imports its own private copy of `htmx.org` and would register the extension on that
// copy instead of the one actually processing the page's hx-* attributes.
document.addEventListener('DOMContentLoaded', () => {
  import('idiomorph/dist/idiomorph-ext.js');
});
