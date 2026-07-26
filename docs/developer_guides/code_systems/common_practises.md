# Common Practices

## Rendering Tags for Tagged Objects

When displaying objects that inherit from `TaggedModelMixin`, always prefetch tag data to avoid N+1 queries. Use `object.prefetched_tags_json` in templates to access the prefetched tags.

### Implementation

**Query with prefetch:**
```python
from django.db.models import Prefetch

messages_queryset = (
    ChatMessage.objects.filter(chat=session.chat)
    .prefetch_related(
        Prefetch(
            "tagged_items",
            queryset=CustomTaggedItem.objects.select_related("tag", "user"),
            to_attr="prefetched_tagged_items",  # Required attribute name
        )
    )
)
```

### Key Points
- Must use `to_attr="prefetched_tagged_items"` - this exact name is required
- Include `select_related("tag", "user")` for optimal performance

## Using Tom Select for Multiselect Inputs

For rich multiselect UI elements, we use the [Tom Select](https://tom-select.js.org/) JavaScript library. It's versatile and can be configured for simple selection or for creating new items on-the-fly (like tags).

### Initialization

To use it, you need an HTML element, typically a `<select multiple>`, and then you initialize the `TomSelect` object in your JavaScript, pointing it to that element. TomSelect is available globally on the `window` object as `window.TomSelect`.

**HTML:**
```html
<select id="teams-select" multiple class="w-full"></select>
```

**JavaScript:**
```javascript
const selectElement = document.getElementById("teams-select");
const teamsSelect = new TomSelect(selectElement, {
    plugins: ['remove_button'],
    maxItems: null,
});
```
