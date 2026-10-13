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
