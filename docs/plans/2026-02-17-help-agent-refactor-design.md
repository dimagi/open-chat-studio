# Help Module Agent Refactor Design

## Problem

The `apps/help` module has a single agent (code generation) with its logic spread across
`views.py`, `utils.py`, and a prompt template file. A second agent (progress messages) lives
entirely outside the module in `apps/api/views/chat.py`. There is no consistent pattern for
defining agents, validating inputs/outputs, or dispatching requests.

We want to add more help agents and need a clean, extensible structure.

## Goals

1. Single base agent class that all help agents subclass
2. Single Django view that dispatches to any registered agent by name
3. Each agent is self-contained: prompt, input/output schemas, execution logic, validation
4. Pydantic classes validate all inputs and outputs
5. Agents are callable both via HTTP and as Python functions

## Architecture

### Approach: Agent as Pydantic Config Class

Each agent is a Pydantic `BaseModel` subclass (generic over `TInput` and `TOutput`) with a
`run()` method. A registry dict maps agent names to classes. The view validates input via
Pydantic, calls `run()`, and returns the serialized output.

### Base Agent

```python
# apps/help/base.py

class BaseHelpAgent(BaseModel, Generic[TInput, TOutput]):
    name: ClassVar[str]                    # registry key + URL slug
    mode: ClassVar[Literal["high", "low"]] # model tier

    input: TInput

    @classmethod
    def get_system_prompt(cls, input: TInput) -> str: ...

    @classmethod
    def get_user_message(cls, input: TInput) -> str: ...

    def run(self) -> TOutput:
        """Default: build agent, invoke, parse response. Override for custom behavior."""
        ...

    def parse_response(self, response) -> TOutput:
        """Extract and validate output from agent response."""
        ...
```

### Registry

```python
# apps/help/registry.py

AGENT_REGISTRY: dict[str, type[BaseHelpAgent]] = {}

def register_agent(cls):
    AGENT_REGISTRY[cls.name] = cls
    return cls
```

### View & URLs

Single view at `POST /help/<agent_name>/`:

```python
# apps/help/views.py

def run_agent(request, team_slug: str, agent_name: str):
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        return JsonResponse({"error": f"Unknown agent: {agent_name}"}, status=404)

    body = json.loads(request.body)
    agent = agent_cls(input=body)      # Pydantic validates input
    result = agent.run()
    return JsonResponse({"response": result.model_dump()})
```

URL routing:

```python
# apps/help/urls.py
urlpatterns = [
    path("<str:agent_name>/", views.run_agent, name="run_agent"),
]
```

## Concrete Agents

### Code Generation Agent

- **Input:** `query` (str), `context` (str, optional — current code)
- **Output:** `code` (str)
- **Mode:** high
- **Prompt:** `code_generate_system_prompt.md` (existing file)
- **Behavior:** Recursive retry loop (max 3 iterations). On each iteration, invokes the
  agent, validates output via `CodeNode.model_validate()`, and retries with error context
  on validation failure. Falls back to current code if all retries exhaust.
- **Source:** Logic moves from `views.py:code_completion()` and `views.py:_get_system_prompt()`

### Progress Messages Agent

- **Input:** `chatbot_name` (str), `chatbot_description` (str, optional)
- **Output:** `messages` (list[str])
- **Mode:** low
- **Prompt:** `PROGRESS_MESSAGE_PROMPT` (moved from `apps/api/views/chat.py`)
- **Behavior:** Single invocation with `response_format` for structured output.
- **Source:** Logic moves from `apps/api/views/chat.py:get_progress_messages()`

## File Structure

```
apps/help/
├── __init__.py                          # SystemAgentModel + get_system_agent_models (unchanged)
├── apps.py                              # AppConfig (unchanged)
├── agent.py                             # build_system_agent (unchanged, shared infra)
├── base.py                              # NEW: BaseHelpAgent, generic base class
├── registry.py                          # NEW: AGENT_REGISTRY + register_agent
├── views.py                             # REWRITTEN: single run_agent view
├── urls.py                              # REWRITTEN: single dynamic route
├── utils.py                             # KEPT: prompt builder utilities
├── agents/
│   ├── __init__.py                      # imports agents to trigger registration
│   ├── code_generate.py                 # CodeGenerateAgent + schemas
│   └── progress_messages.py             # ProgressMessagesAgent + schemas
├── code_generate_system_prompt.md       # KEPT in place
└── tests.py                             # UPDATED
```

## What Moves

| From | To | What |
|------|----|------|
| `views.py` `code_completion()`, `_get_system_prompt()` | `agents/code_generate.py` | Code gen logic + prompt loading |
| `apps/api/views/chat.py` `PROGRESS_MESSAGE_PROMPT`, `ProgressMessagesSchema`, `get_progress_messages()` | `agents/progress_messages.py` | Progress messages agent |

## What Stays

- `__init__.py` — `SystemAgentModel` is infrastructure, not agent-specific
- `agent.py` — `build_system_agent()` is shared by all agents
- `utils.py` — prompt builder utilities used by code gen agent and potentially elsewhere
- `code_generate_system_prompt.md` — prompt template

## Callers to Update

- **Frontend JS** calling `POST /help/generate_code/` updates to `POST /help/code_generate/`
  (input fields `query` and `context` stay the same)
- **`apps/api/views/chat.py`** `get_progress_messages()` becomes a thin wrapper calling
  `ProgressMessagesAgent.run()` directly

## Error Handling

1. **Input validation** — Pydantic `ValidationError` → 400
2. **Agent not found** — Registry miss → 404
3. **Execution failure** — Caught in view → 500, logged
4. **Per-agent validation** — Handled internally (e.g., code gen retry loop)

## Testing

- Unit test each agent's `run()` directly (mock `build_system_agent`)
- Test the view once for dispatch, 404, 400, 500 (agent-agnostic)
- Keep existing `utils.py` tests unchanged
- Update `apps/api/tests/test_chat_progress.py` mock targets
