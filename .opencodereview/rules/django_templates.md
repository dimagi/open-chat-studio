> Only raise an issue you are confident is a real defect. These are Django templates rendered server-side, with HTMX and Alpine.js for interactivity.

#### Escaping
- `|safe`, `{% autoescape off %}`, or a value built with `mark_safe`, applied to anything a participant or team member can set
- User content interpolated into a `<script>` block or an Alpine expression, where template autoescaping does not protect it

#### URLs and HTMX
- A hardcoded path where `{% url %}` should be used, or a `{% url %}` naming a route that does not exist
- `hx-post`/`hx-put`/`hx-delete` without `{% csrf_token %}` in the posted form or a CSRF header
- `hx-target`/`hx-swap` pointing at an id the template never renders

#### Alpine
- `x-html` fed a value that is not already escaped
- `x-data` duplicating state the server just rendered, so the two can disagree

#### Project Conventions
- `{# ... #}` spread over several lines. Django does not support it; use `{% comment %}{% endcomment %}`
- `{% flag "..." %}` naming a slug that no longer exists
- A new user-facing string left untranslated where the surrounding template uses `{% translate %}`

#### Do Not Report
- Indentation, attribute wrapping, or tag spacing — djlint formats this file
- The djlint rules this project disables: H030, H031, H021, H006, H013, H014, T003
- Tailwind or DaisyUI class lists
