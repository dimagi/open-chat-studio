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
    from langchain_google_vertexai import ChatVertexAI  # noqa: PLC0415 - TID253: heavy lib, slow startup
    return ChatVertexAI(...)
```

Heavy libraries that benefit from lazy loading:
* `langchain_google_vertexai` (~45s import time)
* `langchain_google_genai` (~9s import time)
* `langchain.chat_models` (~5s; pulls in `transformers`)
* `markitdown` (~5s; pulls in `pandas`)
* `langchain_anthropic`, `langchain_openai` (~3s combined)
* `transformers` (~3s)
* `boto3`, `pandas`, `numpy` (when not always needed)

## Enforcing lazy imports
These libraries are listed under `[tool.ruff.lint.flake8-tidy-imports]
banned-module-level-imports` (TID253) in `pyproject.toml`, so a module-level
import of any of them is a lint error. Import them inside the function that
needs them and justify the inline import with the codebase convention:

```python
from markitdown import MarkItDown  # noqa: PLC0415 - TID253: heavy lib, slow startup
```

`scripts/check_inline_imports.py` verifies inline-import justifications across a
package: it treats anything in the TID253 list as config-blessed, and
hoist-tests the rest to catch stale `circular:` claims and unjustified imports.

Use `TYPE_CHECKING` for type hints only:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_google_vertexai import ChatVertexAI
```
