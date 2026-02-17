# Help Module Agent Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor `apps/help` into a unified agent framework where each agent is a self-contained Pydantic class with validated I/O, dispatched by a single Django view.

**Architecture:** Base `BaseHelpAgent` class generic over input/output types. Registry dict maps agent names to classes. Single view at `POST /help/<agent_name>/` validates input, calls `run()`, returns output. Two initial agents: code generation (with retry) and progress messages (structured output).

**Tech Stack:** Django, Pydantic v2, LangChain agents (existing `build_system_agent`)

**Design doc:** `docs/plans/2026-02-17-help-agent-refactor-design.md`

---

### Task 1: Create registry module

**Files:**
- Create: `apps/help/registry.py`
- Test: `apps/help/tests.py` (append)

**Step 1: Write the failing test**

Add to `apps/help/tests.py`:

```python
from apps.help.registry import AGENT_REGISTRY, register_agent


class TestAgentRegistry:
    def test_register_agent_adds_to_registry(self):
        # Use a throwaway class to avoid polluting the registry
        original_registry = AGENT_REGISTRY.copy()
        try:
            @register_agent
            class FakeAgent:
                name = "test_fake"

            assert AGENT_REGISTRY["test_fake"] is FakeAgent
        finally:
            AGENT_REGISTRY.clear()
            AGENT_REGISTRY.update(original_registry)

    def test_register_agent_returns_class_unchanged(self):
        original_registry = AGENT_REGISTRY.copy()
        try:
            @register_agent
            class AnotherFake:
                name = "test_another"

            assert AnotherFake.name == "test_another"
        finally:
            AGENT_REGISTRY.clear()
            AGENT_REGISTRY.update(original_registry)
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/help/tests.py::TestAgentRegistry -v`
Expected: FAIL (cannot import `registry`)

**Step 3: Write implementation**

Create `apps/help/registry.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.help.base import BaseHelpAgent

AGENT_REGISTRY: dict[str, type[BaseHelpAgent]] = {}


def register_agent(cls):
    AGENT_REGISTRY[cls.name] = cls
    return cls
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/help/tests.py::TestAgentRegistry -v`
Expected: PASS

**Step 5: Lint and commit**

```bash
ruff check apps/help/registry.py apps/help/tests.py --fix
ruff format apps/help/registry.py apps/help/tests.py
git add apps/help/registry.py apps/help/tests.py
git commit -m "feat(help): add agent registry module"
```

---

### Task 2: Create BaseHelpAgent base class

**Files:**
- Create: `apps/help/base.py`
- Test: `apps/help/tests.py` (append)

**Step 1: Write the failing test**

Add to `apps/help/tests.py`:

```python
from unittest import mock

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent


class StubInput(BaseModel):
    prompt: str


class StubOutput(BaseModel):
    result: str


class StubAgent(BaseHelpAgent[StubInput, StubOutput]):
    name = "stub"
    mode = "low"

    @classmethod
    def get_system_prompt(cls, input):
        return "You are a stub."

    @classmethod
    def get_user_message(cls, input):
        return input.prompt

    def parse_response(self, response) -> StubOutput:
        return StubOutput(result=response["messages"][-1].text)


class TestBaseHelpAgent:
    @mock.patch("apps.help.base.build_system_agent")
    def test_run_invokes_agent_and_returns_parsed_output(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "messages": [mock.Mock(text="hello")]
        }
        mock_build.return_value = mock_agent

        agent = StubAgent(input=StubInput(prompt="say hi"))
        result = agent.run()

        assert result == StubOutput(result="hello")
        mock_build.assert_called_once_with("low", "You are a stub.")
        mock_agent.invoke.assert_called_once_with(
            {"messages": [{"role": "user", "content": "say hi"}]}
        )
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/help/tests.py::TestBaseHelpAgent -v`
Expected: FAIL (cannot import `base`)

**Step 3: Write implementation**

Create `apps/help/base.py`:

```python
from __future__ import annotations

from typing import ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel

TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class BaseHelpAgent(BaseModel, Generic[TInput, TOutput]):
    """Base class for help agents.

    Subclasses must define:
    - name: ClassVar[str] — registry key and URL slug
    - mode: ClassVar[Literal["high", "low"]] — model tier
    - get_system_prompt(input) — build the system prompt
    - get_user_message(input) — build the user message
    - parse_response(response) — extract TOutput from agent response
    """

    name: ClassVar[str]
    mode: ClassVar[Literal["high", "low"]]

    input: TInput

    @classmethod
    def get_system_prompt(cls, input: TInput) -> str:
        raise NotImplementedError

    @classmethod
    def get_user_message(cls, input: TInput) -> str:
        raise NotImplementedError

    def run(self) -> TOutput:
        from apps.help.agent import build_system_agent

        agent = build_system_agent(self.mode, self.get_system_prompt(self.input))
        response = agent.invoke(
            {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]}
        )
        return self.parse_response(response)

    def parse_response(self, response) -> TOutput:
        raise NotImplementedError
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/help/tests.py::TestBaseHelpAgent -v`
Expected: PASS

**Step 5: Lint and commit**

```bash
ruff check apps/help/base.py apps/help/tests.py --fix
ruff format apps/help/base.py apps/help/tests.py
git add apps/help/base.py apps/help/tests.py
git commit -m "feat(help): add BaseHelpAgent base class"
```

---

### Task 3: Create CodeGenerateAgent

**Files:**
- Create: `apps/help/agents/__init__.py`
- Create: `apps/help/agents/code_generate.py`
- Test: `apps/help/tests.py` (append)

This task moves logic from `apps/help/views.py:42-82` (`code_completion`, `_get_system_prompt`) into a self-contained agent class.

**Step 1: Write the failing test**

Add to `apps/help/tests.py`:

```python
import pydantic as pydantic_module

from apps.help.agents.code_generate import CodeGenerateAgent, CodeGenerateInput, CodeGenerateOutput


class TestCodeGenerateAgent:
    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_returns_valid_code(self, mock_build):
        valid_code = "def main(input: str, **kwargs) -> str:\n    return input"
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "messages": [mock.Mock(text=valid_code)]
        }
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode") as mock_code_node:
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="write hello world"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=valid_code)

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_retries_on_validation_error(self, mock_build):
        bad_code = "not valid python"
        good_code = "def main(input: str, **kwargs) -> str:\n    return input"

        mock_agent = mock.Mock()
        mock_agent.invoke.side_effect = [
            {"messages": [mock.Mock(text=bad_code)]},
            {"messages": [mock.Mock(text=good_code)]},
        ]
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode") as mock_code_node:
            mock_code_node.model_validate.side_effect = [
                pydantic_module.ValidationError.from_exception_data("CodeNode", []),
                None,
            ]
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="fix this"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=good_code)
        assert mock_agent.invoke.call_count == 2

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_returns_last_code_after_max_retries(self, mock_build):
        bad_code = "still broken"
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"messages": [mock.Mock(text=bad_code)]}
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode") as mock_code_node:
            mock_code_node.model_validate.side_effect = pydantic_module.ValidationError.from_exception_data(
                "CodeNode", []
            )
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="fix this"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=bad_code)
        # 1 initial + 3 retries = 4 total
        assert mock_agent.invoke.call_count == 4

    def test_input_validates_query_required(self):
        with pytest.raises(pydantic_module.ValidationError):
            CodeGenerateInput()

    def test_input_context_defaults_to_empty(self):
        inp = CodeGenerateInput(query="hello")
        assert inp.context == ""
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/help/tests.py::TestCodeGenerateAgent -v`
Expected: FAIL (cannot import `agents.code_generate`)

**Step 3: Write implementation**

Create `apps/help/agents/__init__.py`:

```python
from apps.help.agents import code_generate  # noqa: F401
```

Create `apps/help/agents/code_generate.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar, Literal

import pydantic
from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent

logger = logging.getLogger("ocs.help")

_system_prompt = None


def _get_system_prompt():
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent.parent / "code_generate_system_prompt.md").read_text()
    return _system_prompt


class CodeGenerateInput(BaseModel):
    query: str
    context: str = ""


class CodeGenerateOutput(BaseModel):
    code: str


@register_agent
class CodeGenerateAgent(BaseHelpAgent[CodeGenerateInput, CodeGenerateOutput]):
    name: ClassVar[str] = "code_generate"
    mode: ClassVar[Literal["high", "low"]] = "high"
    max_retries: ClassVar[int] = 3

    @classmethod
    def get_system_prompt(cls, input: CodeGenerateInput) -> str:
        raise NotImplementedError("Use _build_system_prompt instead")

    @classmethod
    def get_user_message(cls, input: CodeGenerateInput) -> str:
        return input.query

    def run(self) -> CodeGenerateOutput:
        from apps.pipelines.nodes.nodes import DEFAULT_FUNCTION

        current_code = self.input.context
        if current_code == DEFAULT_FUNCTION:
            current_code = ""

        return self._run_with_retry(current_code, error=None, iteration=0)

    def _run_with_retry(self, current_code: str, error: str | None, iteration: int) -> CodeGenerateOutput:
        if iteration > self.max_retries:
            return CodeGenerateOutput(code=current_code)

        system_prompt = self._build_system_prompt(current_code, error)

        from apps.help.agent import build_system_agent

        agent = build_system_agent(self.mode, system_prompt)
        response = agent.invoke(
            {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]}
        )

        response_code = response["messages"][-1].text

        from apps.pipelines.nodes.nodes import CodeNode

        try:
            CodeNode.model_validate({"code": response_code, "name": "code", "node_id": "code", "django_node": None})
        except pydantic.ValidationError as e:
            return self._run_with_retry(response_code, error=str(e), iteration=iteration + 1)

        return CodeGenerateOutput(code=response_code)

    def _build_system_prompt(self, current_code: str, error: str | None) -> str:
        system_prompt = _get_system_prompt()
        prompt_context = {"current_code": "", "error": ""}

        if current_code:
            prompt_context["current_code"] = f"The current function definition is:\n\n{current_code}"
        if error:
            prompt_context["error"] = f"\nThe current function has the following error. Try to resolve it:\n\n{error}"

        system_prompt = system_prompt.format(**prompt_context).strip()
        system_prompt += (
            "\n\nIMPORTANT: Start your response with exactly"
            " `def main(input: str, **kwargs) -> str:` and nothing else before it."
        )
        return system_prompt

    def parse_response(self, response) -> CodeGenerateOutput:
        raise NotImplementedError("CodeGenerateAgent uses custom run()")
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/help/tests.py::TestCodeGenerateAgent -v`
Expected: PASS

**Step 5: Lint and commit**

```bash
ruff check apps/help/agents/ apps/help/tests.py --fix
ruff format apps/help/agents/ apps/help/tests.py
git add apps/help/agents/ apps/help/tests.py
git commit -m "feat(help): add CodeGenerateAgent"
```

---

### Task 4: Create ProgressMessagesAgent

**Files:**
- Create: `apps/help/agents/progress_messages.py`
- Modify: `apps/help/agents/__init__.py`
- Test: `apps/help/tests.py` (append)

This task moves logic from `apps/api/views/chat.py:47-64` (`PROGRESS_MESSAGE_PROMPT`) and `apps/api/views/chat.py:555-567` (`get_progress_messages`, `ProgressMessagesSchema`) into a self-contained agent class.

**Step 1: Write the failing test**

Add to `apps/help/tests.py`:

```python
from apps.help.agents.progress_messages import (
    ProgressMessagesAgent,
    ProgressMessagesInput,
    ProgressMessagesOutput,
)


class TestProgressMessagesAgent:
    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_run_returns_messages(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": mock.Mock(messages=["Thinking...", "Almost there..."])
        }
        mock_build.return_value = mock_agent

        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name="TestBot", chatbot_description="A test bot")
        )
        result = agent.run()

        assert result == ProgressMessagesOutput(messages=["Thinking...", "Almost there..."])
        mock_build.assert_called_once()
        # Verify response_format was passed
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs["response_format"] is ProgressMessagesOutput

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_user_message_includes_name_and_description(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": mock.Mock(messages=["Working..."])
        }
        mock_build.return_value = mock_agent

        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name="MyBot", chatbot_description="Helps with tasks")
        )
        agent.run()

        call_args = mock_agent.invoke.call_args[0][0]
        user_message = call_args["messages"][0]["content"]
        assert "MyBot" in user_message
        assert "Helps with tasks" in user_message

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_user_message_excludes_empty_description(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": mock.Mock(messages=["Working..."])
        }
        mock_build.return_value = mock_agent

        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name="MyBot")
        )
        agent.run()

        call_args = mock_agent.invoke.call_args[0][0]
        user_message = call_args["messages"][0]["content"]
        assert "Description:" not in user_message

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_run_returns_empty_list_when_none(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": mock.Mock(messages=None)
        }
        mock_build.return_value = mock_agent

        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name="TestBot")
        )
        result = agent.run()

        assert result == ProgressMessagesOutput(messages=[])
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/help/tests.py::TestProgressMessagesAgent -v`
Expected: FAIL (cannot import `agents.progress_messages`)

**Step 3: Write implementation**

Create `apps/help/agents/progress_messages.py`:

```python
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent

PROGRESS_MESSAGE_PROMPT = """\
You will be generating progress update messages that are displayed to users while they \
wait for a chatbot to respond. These messages should keep users engaged and informed \
during the wait time.

Here are the guidelines for creating effective progress messages:

- Keep messages SHORT - aim for 2-4 words maximum
- Use an encouraging, friendly, and slightly playful tone
- Messages should feel dynamic and suggest active work is happening
- Vary the style and wording across different messages
- Focus on the process (e.g., "thinking", "analyzing", "processing") rather than making promises about results
- Avoid technical jargon or overly complex language
- Don't make specific claims about what the answer will contain
- Don't apologize for wait times or sound negative

Generate message options that could be rotated or randomly displayed to users.
Each message should feel fresh and distinct from the others."""


class ProgressMessagesInput(BaseModel):
    chatbot_name: str
    chatbot_description: str = ""


class ProgressMessagesOutput(BaseModel):
    messages: list[str]


@register_agent
class ProgressMessagesAgent(BaseHelpAgent[ProgressMessagesInput, ProgressMessagesOutput]):
    name: ClassVar[str] = "progress_messages"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: ProgressMessagesInput) -> str:
        return PROGRESS_MESSAGE_PROMPT

    @classmethod
    def get_user_message(cls, input: ProgressMessagesInput) -> str:
        message = f"Please generate 30 progress messages for this chatbot:\nName: '{input.chatbot_name}'"
        if input.chatbot_description:
            message += f"\nDescription: '{input.chatbot_description}'"
        return message

    def run(self) -> ProgressMessagesOutput:
        from apps.help.agent import build_system_agent

        agent = build_system_agent(
            self.mode,
            self.get_system_prompt(self.input),
            response_format=ProgressMessagesOutput,
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]}
        )
        messages = result["structured_response"].messages or []
        return ProgressMessagesOutput(messages=messages)

    def parse_response(self, response) -> ProgressMessagesOutput:
        raise NotImplementedError("ProgressMessagesAgent uses custom run()")
```

Update `apps/help/agents/__init__.py`:

```python
from apps.help.agents import code_generate  # noqa: F401
from apps.help.agents import progress_messages  # noqa: F401
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/help/tests.py::TestProgressMessagesAgent -v`
Expected: PASS

**Step 5: Lint and commit**

```bash
ruff check apps/help/agents/ apps/help/tests.py --fix
ruff format apps/help/agents/ apps/help/tests.py
git add apps/help/agents/ apps/help/tests.py
git commit -m "feat(help): add ProgressMessagesAgent"
```

---

### Task 5: Rewrite view and URLs

**Files:**
- Modify: `apps/help/views.py`
- Modify: `apps/help/urls.py`
- Test: `apps/help/tests.py` (append)

**Step 1: Write the failing tests**

Add to `apps/help/tests.py`. These tests need a Django test client. Use `pytest.mark.django_db` and Django's `RequestFactory`:

```python
import json

from django.test import RequestFactory

from apps.help.views import run_agent


class TestRunAgentView:
    def _make_request(self, agent_name, body, user=None):
        factory = RequestFactory()
        request = factory.post(
            f"/help/{agent_name}/",
            data=json.dumps(body),
            content_type="application/json",
        )
        if user:
            request.user = user
        # Call view directly, bypassing decorators for unit test
        return run_agent.__wrapped__(request, team_slug="test-team", agent_name=agent_name)

    def test_unknown_agent_returns_404(self):
        response = self._make_request("nonexistent", {})
        assert response.status_code == 404

    def test_invalid_input_returns_400(self):
        # code_generate requires "query" field
        response = self._make_request("code_generate", {"bad": "data"})
        assert response.status_code == 400

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_successful_agent_call(self, mock_build):
        valid_code = "def main(input: str, **kwargs) -> str:\n    return input"
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"messages": [mock.Mock(text=valid_code)]}
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode"):
            response = self._make_request("code_generate", {"query": "write code"})

        assert response.status_code == 200
        data = json.loads(response.content)
        assert "response" in data

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_agent_error_returns_500(self, mock_build):
        mock_build.side_effect = RuntimeError("boom")
        response = self._make_request("code_generate", {"query": "write code"})
        assert response.status_code == 500
        data = json.loads(response.content)
        assert "error" in data
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/help/tests.py::TestRunAgentView -v`
Expected: FAIL (view doesn't exist yet / old view)

**Step 3: Rewrite views.py**

Replace the contents of `apps/help/views.py` with:

```python
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from pydantic import ValidationError

import apps.help.agents  # noqa: F401 — trigger agent registration
from apps.help.registry import AGENT_REGISTRY
from apps.teams.decorators import login_and_team_required

logger = logging.getLogger("ocs.help")


@require_POST
@login_and_team_required
@csrf_exempt
def run_agent(request, team_slug: str, agent_name: str):
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        return JsonResponse({"error": f"Unknown agent: {agent_name}"}, status=404)

    try:
        body = json.loads(request.body)
        agent = agent_cls(input=body)
    except (json.JSONDecodeError, ValidationError) as e:
        return JsonResponse({"error": str(e)}, status=400)

    try:
        result = agent.run()
        return JsonResponse({"response": result.model_dump()})
    except Exception:
        logger.exception("Agent '%s' failed.", agent_name)
        return JsonResponse({"error": "An error occurred."}, status=500)
```

**Step 4: Rewrite urls.py**

Replace the contents of `apps/help/urls.py` with:

```python
from django.urls import path

from apps.help import views

app_name = "help"

urlpatterns = [
    path("<str:agent_name>/", views.run_agent, name="run_agent"),
]
```

**Step 5: Run tests to verify they pass**

Run: `pytest apps/help/tests.py::TestRunAgentView -v`
Expected: PASS

Note: The `__wrapped__` approach in tests bypasses `@login_and_team_required`. If that decorator doesn't expose `__wrapped__`, adjust the test to use Django's test client with a logged-in user instead. Check decorator implementation and adapt.

**Step 6: Lint and commit**

```bash
ruff check apps/help/views.py apps/help/urls.py apps/help/tests.py --fix
ruff format apps/help/views.py apps/help/urls.py apps/help/tests.py
git add apps/help/views.py apps/help/urls.py apps/help/tests.py
git commit -m "feat(help): rewrite view and URLs for agent dispatch"
```

---

### Task 6: Update frontend caller

**Files:**
- Modify: `assets/javascript/apps/pipeline/api/api.ts:70-72`

**Step 1: Update the URL**

In `assets/javascript/apps/pipeline/api/api.ts`, change line 71 from:

```typescript
return this.makeRequest<AiHelpResponse>("post", `/help/generate_code/`, {query: prompt, context: currentCode});
```

to:

```typescript
return this.makeRequest<AiHelpResponse>("post", `/help/code_generate/`, {query: prompt, context: currentCode});
```

The response shape changes from `{"response": "<code string>"}` to `{"response": {"code": "<code string>"}}`. Check how `AiHelpResponse` is defined and how the response is consumed. Update the caller to extract `.response.code` if needed.

**Step 2: Run lint**

```bash
npm run lint assets/javascript/apps/pipeline/api/api.ts
npm run type-check
```

**Step 3: Commit**

```bash
git add assets/javascript/apps/pipeline/api/api.ts
git commit -m "feat(help): update frontend to use new agent endpoint"
```

---

### Task 7: Update apps/api/views/chat.py to use ProgressMessagesAgent

**Files:**
- Modify: `apps/api/views/chat.py:47-64,551-567`
- Modify: `apps/api/tests/test_chat_progress.py`

**Step 1: Update get_progress_messages**

In `apps/api/views/chat.py`:

1. Remove `PROGRESS_MESSAGE_PROMPT` (lines 47-64)
2. Remove `ProgressMessagesSchema` (lines 551-552)
3. Replace `get_progress_messages` (lines 555-567) with:

```python
def get_progress_messages(chatbot_name, chatbot_description) -> list[str]:
    from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput

    try:
        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name=chatbot_name, chatbot_description=chatbot_description)
        )
        return agent.run().messages
    except Exception:
        logger.exception("Failed to generate progress messages for chatbot '%s'", chatbot_name)
        return []
```

**Step 2: Update test mock targets**

In `apps/api/tests/test_chat_progress.py`:

- The `TestGetProgressMessages` tests (lines 16-64) mock `apps.help.agent.build_system_agent`. These now need to mock `apps.help.agents.progress_messages.build_system_agent` instead.
- Update the mock decorator on each test in `TestGetProgressMessages`:

```python
@mock.patch("apps.help.agents.progress_messages.build_system_agent")
```

- The `TestGetProgressMessage` tests (lines 67-99) mock `apps.api.views.chat.get_progress_messages` — these stay unchanged since `get_progress_messages` still lives in the same module.

**Step 3: Run tests**

```bash
pytest apps/api/tests/test_chat_progress.py -v
pytest apps/help/tests.py -v
```

Expected: All PASS

**Step 4: Lint and commit**

```bash
ruff check apps/api/views/chat.py apps/api/tests/test_chat_progress.py --fix
ruff format apps/api/views/chat.py apps/api/tests/test_chat_progress.py
git add apps/api/views/chat.py apps/api/tests/test_chat_progress.py
git commit -m "refactor(help): migrate progress messages to ProgressMessagesAgent"
```

---

### Task 8: Clean up old code and run full test suite

**Files:**
- Verify: `apps/help/views.py` — no leftover `code_completion`, `_get_system_prompt`, `_system_prompt`
- Verify: `apps/help/tests.py` — existing `test_get_python_node_coder_prompt` and `TestExtractFunctionSignature` still pass
- Verify: `apps/help/utils.py` — unchanged

**Step 1: Verify no stale imports or references**

Search for any remaining references to the old view function name:

```bash
rg "pipeline_generate_code" --type py --type ts --type html
```

Should return zero results (only the git history).

Search for any remaining references to old code paths:

```bash
rg "code_completion" --type py
rg "_get_system_prompt" apps/help/
```

Should return zero results outside of git history.

**Step 2: Run full test suite for affected apps**

```bash
pytest apps/help/tests.py -v
pytest apps/api/tests/test_chat_progress.py -v
```

Expected: All PASS

**Step 3: Lint all changed files**

```bash
ruff check apps/help/ --fix
ruff format apps/help/
```

**Step 4: Final commit**

```bash
git add -A apps/help/
git commit -m "chore(help): clean up old code after agent refactor"
```
