# Standard Practices

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

