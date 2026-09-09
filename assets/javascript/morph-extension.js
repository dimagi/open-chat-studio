// Registers `hx-ext="morph"` against the page's real htmx instance (django-htmx's own
// vendored copy, loaded by `{% htmx_script %}` right before this script in templates/web/base.html).
// Both scripts use `defer`, so this always runs after that one, and before DOMContentLoaded --
// no async gap where a click could beat registration, unlike a dynamic import on DOMContentLoaded.
//
// Importing the classic build rather than "idiomorph/htmx" matters too: the latter re-imports
// its own private copy of `htmx.org` and would register the extension on that copy instead of
// the one actually processing the page's hx-* attributes.
import 'idiomorph/dist/idiomorph-ext.js';
