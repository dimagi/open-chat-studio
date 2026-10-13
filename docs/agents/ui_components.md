# UI Components

Shared UI building blocks. Use these instead of browser built-ins or one-off implementations.

## Confirmation dialog

Use `confirmThenTrigger` (`assets/javascript/alertify.js`), not `confirm()` or `hx-confirm`. The element needs a unique `id` and `hx-trigger="confirmed"`:

```html
<button id="archive-chatbot-button"
        hx-post="{% url 'chatbots:archive' request.team.slug experiment.id %}"
        hx-trigger="confirmed"
        onclick="confirmThenTrigger('archive-chatbot-button', 'Archive this chatbot?')">
```

To reload or leave the page afterwards, the view returns `django_htmx.http.HttpResponseClientRedirect`.

## Toast notifications

Server side: use Django `messages`; `templates/web/components/messages.html` shows them as toasts. Client side: `alertify.notify(message, type, seconds)`.
