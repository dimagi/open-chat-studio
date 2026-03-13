# Performance Patterns

## Lazy Loading Heavy Imports
Avoid importing heavy AI/ML libraries at module level to keep Django startup time fast:
```python
# ❌ BAD - imports at module level (slow startup)
from langchain_google_vertexai import ChatVertexAI
from langchain_anthropic import ChatAnthropic

def get_model():
    return ChatVertexAI(...)

# ✅ GOOD - lazy import inside method (fast startup)
def get_model():
    from langchain_google_vertexai import ChatVertexAI
    return ChatVertexAI(...)
```

Heavy libraries that benefit from lazy loading:
* `langchain_google_vertexai` (~45s import time)
* `langchain_google_genai` (~9s import time)
* `langchain_anthropic`, `langchain_openai` (~3s combined)
* `boto3`, `pandas`, `numpy` (when not always needed)

Use `TYPE_CHECKING` for type hints only:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_google_vertexai import ChatVertexAI
```
