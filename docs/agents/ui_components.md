# UI Components

Shared UI building blocks. Use these instead of browser built-ins or one-off implementations.

## Confirmation dialog

Use the helpers in `assets/javascript/alertify.js`, never `confirm()` or `hx-confirm`. They also work inside an open `<dialog>` modal.

htmx request: `confirmThenTrigger`. The element needs a unique `id` and `hx-trigger="confirmed"`:

```html
<button id="archive-chatbot-button"
        hx-post="{% url 'chatbots:archive' request.team.slug experiment.id %}"
        hx-trigger="confirmed"
        onclick="confirmThenTrigger('archive-chatbot-button', 'Archive this chatbot?')">
```

To reload or leave the page afterwards, the view returns `django_htmx.http.HttpResponseClientRedirect`.

Plain form POST: `<form method="post" onsubmit="confirmThenSubmit(event, 'Remove this member?')">`.

JavaScript: `if (!(await confirmDialog('Delete this filter?'))) return;`

When the confirmation needs input or more than a sentence of context, use a `<dialog class="modal">` instead (e.g. the Block dialog in `templates/participants/single_participant_home.html`).

## Toast notifications

Server side: use Django `messages`; `templates/web/components/messages.html` shows them as toasts. Client side: `alertify.notify(message, type, seconds)`.

## Template components

Shared markup is a [django-cotton](https://django-cotton.com/docs/components) component under `templates/cotton/`, used as an HTML tag:

```html
<c-ui.chip :url="session.get_absolute_url" :label="session.name" truncate />
<c-ui.help>{% translate "Shown on hover." %}</c-ui.help>
```

- `ui/` holds presentation primitives (button, badge, chip, help, alert, modal, tabs, dropdown). `app/` holds domain pieces composed from them. The tag name is the path: `<c-ui.copy-chip>` is `templates/cotton/ui/copy_chip.html`.
- Start every component with a usage example in a `{% comment %}` block and a `<c-vars>` line declaring every parameter, each with a default (`<c-vars url="" truncate="" />`). A parameter declared without a default is not set when the caller omits it, so a same-named variable in the calling template's context shows through. Anything not declared lands in `{{ attrs }}`.
- Put `{{ attrs }}` on the root element so callers can pass `hx-*`, `x-*`, `id` and `aria-*` without the component listing them. When the root has classes of its own, write `{{ attrs|with_classes:"btn btn-sm" }}` instead (from `apps/web/templatetags/default_tags.py`): it prepends those classes to any `class` the caller passed and leaves declared parameters out. cotton's own `merge` filter prints declared parameters as attributes, so do not use it.
- `:name="expr"` passes a context variable or Python literal, `name="text"` passes a string, a bare `name` passes `True`. Inside a component tag a single colon always means a Django expression, so Alpine's `:bind` shorthand is written `::bind`; `@click` and `x-*` pass through unchanged.
- Once a `ui/` primitive exists, use it instead of writing the daisyUI classes by hand.
- From Python (a table column, a model method), render a component with `apps.generics.components.render_component("ui.chip", chip=chip)`; a `slot` argument is escaped unless marked safe. In a `template_code` string, which never goes through the template loader, write the tag form: `{% cotton ui.session-id-chip :external_id="record.external_id" %}{% endcotton %}`.
- `templates/generic/` keeps only what is not a component: page layouts that templates `extends` (`object_home.html`, `object_form.html`), the table action buttons rendered by `apps/generics/actions.py`, the referenced-objects modal rendered by `apps/generics/referenced_objects.py`, and `attrs.html`, which spreads an attribute dict onto a plain element.
- `{% partialdef %}` is for repeating a fragment within one page and for HTMX fragment responses, not for shared components. A view that returns a single component uses `django_cotton.utils.render_component`.
- Add every new component to the UI Gallery (`/admin/ui-gallery/`, `templates/admin/ui_gallery.html`) and check it in both themes.
- Test a component by rendering a template string through the cotton compiler, as in `apps/web/tests/test_template_components.py`.
