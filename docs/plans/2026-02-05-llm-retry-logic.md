# LLM Retry Logic Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add retry support for rate limit errors across all LLM service usages to improve resilience.

**Architecture:** Create a centralized retry configuration module. Apply LangGraph's `RetryPolicy` for pipeline nodes and LangChain's `.with_retry()` for direct LLM invocations. Handle rate limit exceptions from OpenAI, Anthropic, and Google providers.

**Tech Stack:** LangGraph RetryPolicy, LangChain `.with_retry()`, tenacity for exponential backoff

---

## Background

### Exception Types to Handle
- `openai.RateLimitError` - OpenAI rate limits
- `anthropic.RateLimitError` - Anthropic rate limits
- `google.api_core.exceptions.ResourceExhausted` - Google/VertexAI rate limits

### Two Retry Mechanisms
1. **LangGraph RetryPolicy** - For pipeline nodes, added via `state_graph.add_node(..., retry_policy=...)`
2. **LangChain `.with_retry()`** - For direct LLM invocations (returns `RunnableRetry`)

### Important Caveat
`.with_retry()` returns a `RunnableRetry` which loses chat-specific methods like `bind_tools()`. Therefore:
- Pipeline nodes using `create_agent()` MUST use LangGraph's `RetryPolicy` at the node level
- Direct invocations NOT using `bind_tools()` can use `.with_retry()`

---

## Task 1: Create Retry Configuration Module

**Files:**
- Create: `apps/service_providers/llm_service/retry.py`
- Test: `apps/service_providers/llm_service/tests/test_retry.py`

**Step 1: Write the test file**

```python
# apps/service_providers/llm_service/tests/test_retry.py
import pytest
from unittest.mock import MagicMock, patch

from apps.service_providers.llm_service.retry import (
    RATE_LIMIT_EXCEPTIONS,
    get_retry_policy,
    with_llm_retry,
    should_retry_exception,
)


class TestRateLimitExceptions:
    def test_openai_rate_limit_in_exceptions(self):
        import openai
        assert openai.RateLimitError in RATE_LIMIT_EXCEPTIONS

    def test_anthropic_rate_limit_in_exceptions(self):
        import anthropic
        assert anthropic.RateLimitError in RATE_LIMIT_EXCEPTIONS

    def test_google_resource_exhausted_in_exceptions(self):
        from google.api_core.exceptions import ResourceExhausted
        assert ResourceExhausted in RATE_LIMIT_EXCEPTIONS


class TestGetRetryPolicy:
    def test_returns_retry_policy(self):
        from langgraph.types import RetryPolicy
        policy = get_retry_policy()
        assert isinstance(policy, RetryPolicy)

    def test_default_max_attempts(self):
        policy = get_retry_policy()
        assert policy.max_attempts == 3

    def test_custom_max_attempts(self):
        policy = get_retry_policy(max_attempts=5)
        assert policy.max_attempts == 5


class TestShouldRetryException:
    def test_retries_openai_rate_limit(self):
        import openai
        exc = openai.RateLimitError("rate limited", response=MagicMock(), body=None)
        assert should_retry_exception(exc) is True

    def test_retries_anthropic_rate_limit(self):
        import anthropic
        exc = anthropic.RateLimitError("rate limited")
        assert should_retry_exception(exc) is True

    def test_retries_google_resource_exhausted(self):
        from google.api_core.exceptions import ResourceExhausted
        exc = ResourceExhausted("rate limited")
        assert should_retry_exception(exc) is True

    def test_does_not_retry_other_exceptions(self):
        exc = ValueError("some error")
        assert should_retry_exception(exc) is False


class TestWithLlmRetry:
    def test_returns_runnable_retry(self):
        from langchain_core.runnables import RunnableRetry
        mock_runnable = MagicMock()
        mock_runnable.with_retry = MagicMock(return_value=MagicMock(spec=RunnableRetry))

        result = with_llm_retry(mock_runnable)

        mock_runnable.with_retry.assert_called_once()
        call_kwargs = mock_runnable.with_retry.call_args.kwargs
        assert call_kwargs["stop_after_attempt"] == 3
        assert call_kwargs["wait_exponential_jitter"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/service_providers/llm_service/tests/test_retry.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'apps.service_providers.llm_service.retry'"

**Step 3: Create the retry module**

```python
# apps/service_providers/llm_service/retry.py
"""
Retry configuration for LLM service calls.

This module provides centralized retry logic for handling rate limit errors
from various LLM providers (OpenAI, Anthropic, Google).

Two approaches are provided:
1. `get_retry_policy()` - Returns a LangGraph RetryPolicy for use with StateGraph.add_node()
2. `with_llm_retry()` - Wraps a Runnable with .with_retry() for direct invocations

Important: `with_llm_retry()` returns a RunnableRetry which loses chat-specific methods
like `bind_tools()`. For nodes using `create_agent()`, use `get_retry_policy()` instead.
"""
import anthropic
import openai
from google.api_core.exceptions import ResourceExhausted
from langchain_core.runnables import Runnable
from langgraph.types import RetryPolicy

# Tuple of exception types that indicate rate limiting
RATE_LIMIT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    anthropic.RateLimitError,
    ResourceExhausted,
)

# Default retry configuration
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_INTERVAL = 1.0  # seconds
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_INTERVAL = 60.0  # seconds


def should_retry_exception(exc: Exception) -> bool:
    """
    Determine if an exception should trigger a retry.

    Returns True for rate limit errors from supported providers.
    """
    return isinstance(exc, RATE_LIMIT_EXCEPTIONS)


def get_retry_policy(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_interval: float = DEFAULT_INITIAL_INTERVAL,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_interval: float = DEFAULT_MAX_INTERVAL,
) -> RetryPolicy:
    """
    Get a LangGraph RetryPolicy configured for rate limit handling.

    Use this with StateGraph.add_node(..., retry_policy=get_retry_policy())
    for pipeline nodes.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        initial_interval: Initial wait time between retries in seconds (default: 1.0)
        backoff_factor: Multiplier for wait time after each retry (default: 2.0)
        max_interval: Maximum wait time between retries in seconds (default: 60.0)

    Returns:
        RetryPolicy configured for rate limit handling
    """
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_interval=initial_interval,
        backoff_factor=backoff_factor,
        max_interval=max_interval,
        jitter=True,
        retry_on=should_retry_exception,
    )


def with_llm_retry(
    runnable: Runnable,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Runnable:
    """
    Wrap a Runnable with retry logic for rate limit handling.

    WARNING: This returns a RunnableRetry which loses chat-specific methods
    like `bind_tools()`. Do NOT use this for models passed to `create_agent()`.
    Use `get_retry_policy()` with StateGraph.add_node() instead.

    Args:
        runnable: The Runnable (e.g., BaseChatModel) to wrap
        max_attempts: Maximum number of retry attempts (default: 3)

    Returns:
        Runnable wrapped with retry logic
    """
    return runnable.with_retry(
        retry_if_exception_type=RATE_LIMIT_EXCEPTIONS,
        wait_exponential_jitter=True,
        stop_after_attempt=max_attempts,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/service_providers/llm_service/tests/test_retry.py -v`
Expected: PASS

**Step 5: Lint the files**

Run: `ruff check apps/service_providers/llm_service/retry.py apps/service_providers/llm_service/tests/test_retry.py --fix && ruff format apps/service_providers/llm_service/retry.py apps/service_providers/llm_service/tests/test_retry.py`
Expected: Clean output

**Step 6: Commit**

```bash
git add apps/service_providers/llm_service/retry.py apps/service_providers/llm_service/tests/test_retry.py
git commit -m "feat: add centralized retry configuration for LLM rate limits"
```

---

## Task 2: Add RetryPolicy to Pipeline Graph Nodes

**Files:**
- Modify: `apps/pipelines/graph.py:185-208`
- Test: `apps/pipelines/tests/test_graph.py`

**Step 1: Write the failing test**

Add to the existing test file (create if doesn't exist):

```python
# apps/pipelines/tests/test_graph.py
import pytest
from unittest.mock import MagicMock, patch

from apps.pipelines.graph import PipelineGraph


class TestPipelineGraphRetryPolicy:
    @patch("apps.pipelines.graph.get_retry_policy")
    def test_nodes_added_with_retry_policy(self, mock_get_retry_policy):
        """Verify that nodes are added to the graph with a retry policy."""
        from langgraph.types import RetryPolicy

        mock_policy = RetryPolicy()
        mock_get_retry_policy.return_value = mock_policy

        # Create a minimal pipeline structure
        mock_pipeline = MagicMock()
        mock_start_node = MagicMock()
        mock_start_node.flow_id = "start"
        mock_start_node.label = "Start"
        mock_start_node.type = "StartNode"
        mock_start_node.params = {}

        mock_end_node = MagicMock()
        mock_end_node.flow_id = "end"
        mock_end_node.label = "End"
        mock_end_node.type = "EndNode"
        mock_end_node.params = {}

        mock_pipeline.node_set.all.return_value = [mock_start_node, mock_end_node]
        mock_pipeline.data = {"edges": [{"id": "e1", "source": "start", "target": "end"}]}

        with patch.object(PipelineGraph, "_check_for_cycles", return_value=False):
            graph = PipelineGraph.build_from_pipeline(mock_pipeline)

            # Mock the state_graph to capture add_node calls
            with patch("apps.pipelines.graph.StateGraph") as MockStateGraph:
                mock_state_graph = MagicMock()
                MockStateGraph.return_value = mock_state_graph

                graph.build_runnable()

                # Verify add_node was called with retry_policy
                for call in mock_state_graph.add_node.call_args_list:
                    _, kwargs = call
                    assert "retry_policy" in kwargs
                    assert kwargs["retry_policy"] == mock_policy
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/pipelines/tests/test_graph.py::TestPipelineGraphRetryPolicy -v`
Expected: FAIL with assertion error about retry_policy

**Step 3: Modify the graph.py to add retry policy**

```python
# apps/pipelines/graph.py
# Add import at top of file (after line 6):
from apps.service_providers.llm_service.retry import get_retry_policy

# Modify _add_nodes_to_graph method (lines 185-208):
    def _add_nodes_to_graph(self, state_graph: StateGraph, nodes: list[Node]):
        if self.end_node not in nodes:
            raise PipelineBuildError(
                f"{EndNode.model_config['json_schema_extra'].label} node is not reachable "
                f"from {StartNode.model_config['json_schema_extra'].label} node",
                node_id=self.end_node.id,
            )

        retry_policy = get_retry_policy()

        for node in nodes:
            try:
                node_instance = node.pipeline_node_instance
                incoming_nodes = [edge.source for edge in self.edges if edge.target == node.id]
                if isinstance(node_instance, PipelineRouterNode):
                    edge_map = self.conditional_edge_map[node.id]
                    router_function = node_instance.build_router_function(edge_map, incoming_nodes)
                    state_graph.add_node(node.id, router_function, retry_policy=retry_policy)
                else:
                    outgoing_nodes = [edge.target for edge in self.edges if edge.source == node.id]
                    state_graph.add_node(
                        node.id,
                        partial(node_instance.process, incoming_nodes, outgoing_nodes),
                        retry_policy=retry_policy,
                    )
            except ValidationError as ex:
                raise PipelineNodeBuildError(ex) from ex
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/pipelines/tests/test_graph.py::TestPipelineGraphRetryPolicy -v`
Expected: PASS

**Step 5: Run existing graph tests to ensure no regression**

Run: `pytest apps/pipelines/tests/test_graph.py -v`
Expected: All tests PASS

**Step 6: Lint the file**

Run: `ruff check apps/pipelines/graph.py --fix && ruff format apps/pipelines/graph.py`
Expected: Clean output

**Step 7: Commit**

```bash
git add apps/pipelines/graph.py apps/pipelines/tests/test_graph.py
git commit -m "feat: add retry policy to pipeline graph nodes for rate limit resilience"
```

---

## Task 3: Add Retry to Direct LLM Invocations in Analysis Module

**Files:**
- Modify: `apps/analysis/tasks.py:40`
- Modify: `apps/analysis/translation.py:52`
- Test: `apps/analysis/tests/test_retry.py`

**Step 1: Write the failing test**

```python
# apps/analysis/tests/test_retry.py
import pytest
from unittest.mock import MagicMock, patch


class TestTranscriptAnalysisRetry:
    @patch("apps.analysis.tasks.with_llm_retry")
    @patch("apps.analysis.tasks.get_model_parameters")
    def test_llm_wrapped_with_retry(self, mock_get_params, mock_with_retry):
        """Verify that the LLM is wrapped with retry logic."""
        from apps.analysis.tasks import process_transcript_analysis

        mock_get_params.return_value = {"temperature": 0.1}
        mock_llm = MagicMock()
        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm

        mock_analysis = MagicMock()
        mock_analysis.llm_provider.get_llm_service.return_value = mock_llm_service
        mock_analysis.llm_provider_model.name = "gpt-4"
        mock_analysis.translation_language = None
        mock_analysis.sessions.all.return_value.prefetch_related.return_value = []
        mock_analysis.queries.all.return_value.order_by.return_value = []

        with patch("apps.analysis.tasks.TranscriptAnalysis.objects.select_related") as mock_select:
            mock_select.return_value.get.return_value = mock_analysis
            with patch("apps.analysis.tasks.current_team"):
                with patch("apps.analysis.tasks.ProgressRecorder"):
                    try:
                        process_transcript_analysis(1)
                    except Exception:
                        pass  # We just want to verify the retry wrapper was called

        mock_with_retry.assert_called_once_with(mock_llm)


class TestTranslationRetry:
    @patch("apps.analysis.translation.with_llm_retry")
    @patch("apps.analysis.translation.get_model_parameters")
    def test_llm_wrapped_with_retry(self, mock_get_params, mock_with_retry):
        """Verify that the translation LLM is wrapped with retry logic."""
        from apps.analysis.translation import translate_messages_with_llm

        mock_get_params.return_value = {"temperature": 0.1}
        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_llm_with_retry.invoke.return_value.text = "[]"
        mock_with_retry.return_value = mock_llm_with_retry

        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm

        mock_provider = MagicMock()
        mock_provider.get_llm_service.return_value = mock_llm_service

        mock_model = MagicMock()
        mock_model.name = "gpt-4"

        mock_message = MagicMock()
        mock_message.translations = {}
        mock_message.id = 1
        mock_message.content = "Hello"
        mock_message.role = "user"

        with patch("apps.analysis.translation.current_team"):
            translate_messages_with_llm([mock_message], "es", mock_provider, mock_model)

        mock_with_retry.assert_called_once_with(mock_llm)
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/analysis/tests/test_retry.py -v`
Expected: FAIL with "No module named 'apps.analysis.tasks'" or assertion error

**Step 3: Modify analysis/tasks.py**

```python
# apps/analysis/tasks.py
# Add import at top (after line 10):
from apps.service_providers.llm_service.retry import with_llm_retry

# Modify lines 39-40:
            params = get_model_parameters(model_name, temperature=0.1)  # Low temperature for analysis
            llm = with_llm_retry(llm_service.get_chat_model(model_name, **params))
```

**Step 4: Modify analysis/translation.py**

```python
# apps/analysis/translation.py
# Add import at top (after line 4):
from apps.service_providers.llm_service.retry import with_llm_retry

# Modify lines 51-52:
            params = get_model_parameters(model_name, temperature=0.1)
            llm = with_llm_retry(llm_service.get_chat_model(model_name, **params))
```

**Step 5: Run test to verify it passes**

Run: `pytest apps/analysis/tests/test_retry.py -v`
Expected: PASS

**Step 6: Lint the files**

Run: `ruff check apps/analysis/tasks.py apps/analysis/translation.py --fix && ruff format apps/analysis/tasks.py apps/analysis/translation.py`
Expected: Clean output

**Step 7: Commit**

```bash
git add apps/analysis/tasks.py apps/analysis/translation.py apps/analysis/tests/test_retry.py
git commit -m "feat: add retry logic to analysis module LLM calls"
```

---

## Task 4: Add Retry to Prompt Builder Task

**Files:**
- Modify: `apps/experiments/tasks.py:127`
- Test: `apps/experiments/tests/test_tasks_retry.py`

**Step 1: Write the failing test**

```python
# apps/experiments/tests/test_tasks_retry.py
import pytest
from unittest.mock import MagicMock, patch


class TestPromptBuilderRetry:
    @patch("apps.experiments.tasks.with_llm_retry")
    @patch("apps.experiments.tasks.create_conversation")
    @patch("apps.experiments.tasks.LlmProvider.objects.get")
    @patch("apps.experiments.tasks.LlmProviderModel.objects.get")
    @patch("apps.experiments.tasks.CustomUser.objects.get")
    @patch("apps.experiments.tasks.SourceMaterial.objects.filter")
    @patch("apps.experiments.tasks.PromptBuilderHistory.objects.create")
    def test_llm_wrapped_with_retry(
        self,
        mock_history_create,
        mock_source_filter,
        mock_user_get,
        mock_model_get,
        mock_provider_get,
        mock_create_conv,
        mock_with_retry,
    ):
        """Verify that the prompt builder LLM is wrapped with retry logic."""
        from apps.experiments.tasks import get_prompt_builder_response_task

        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_with_retry.return_value = mock_llm_with_retry

        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm
        mock_provider_get.return_value.get_llm_service.return_value = mock_llm_service
        mock_model_get.return_value.name = "gpt-4"
        mock_user_get.return_value = MagicMock()
        mock_source_filter.return_value.first.return_value = None

        mock_conversation = MagicMock()
        mock_conversation.predict.return_value = ("response", 10, 5)
        mock_create_conv.return_value = mock_conversation

        data_dict = {
            "provider": 1,
            "providerModelId": 1,
            "messages": [],
            "prompt": "test prompt",
            "sourceMaterialID": None,
            "temperature": 0.7,
            "inputFormatter": None,
        }

        get_prompt_builder_response_task(team_id=1, user_id=1, data_dict=data_dict)

        mock_with_retry.assert_called_once_with(mock_llm)
        mock_create_conv.assert_called_once()
        # Verify the wrapped LLM was passed to create_conversation
        call_args = mock_create_conv.call_args
        assert call_args[1]["llm"] == mock_llm_with_retry or call_args[0][2] == mock_llm_with_retry
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/experiments/tests/test_tasks_retry.py -v`
Expected: FAIL with assertion error about with_llm_retry not called

**Step 3: Modify experiments/tasks.py**

```python
# apps/experiments/tasks.py
# Add import at top (after line 19):
from apps.service_providers.llm_service.retry import with_llm_retry

# Modify line 127:
    llm = with_llm_retry(llm_service.get_chat_model(llm_provider_model.name, temperature=float(data_dict["temperature"])))
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/experiments/tests/test_tasks_retry.py -v`
Expected: PASS

**Step 5: Lint the file**

Run: `ruff check apps/experiments/tasks.py --fix && ruff format apps/experiments/tasks.py`
Expected: Clean output

**Step 6: Commit**

```bash
git add apps/experiments/tasks.py apps/experiments/tests/test_tasks_retry.py
git commit -m "feat: add retry logic to prompt builder LLM calls"
```

---

## Task 5: Add Retry to Pipeline Nodes Direct LLM Usage

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py:150-151`
- Modify: `apps/pipelines/nodes/mixins.py:354`
- Test: `apps/pipelines/nodes/tests/test_retry.py`

**Step 1: Write the failing test**

```python
# apps/pipelines/nodes/tests/test_retry.py
import pytest
from unittest.mock import MagicMock, patch


class TestLLMResponseRetry:
    @patch("apps.pipelines.nodes.nodes.with_llm_retry")
    def test_llm_response_uses_retry(self, mock_with_retry):
        """Verify LLMResponse wraps LLM with retry."""
        from apps.pipelines.nodes.nodes import LLMResponse

        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_llm_with_retry.invoke.return_value.content = "response"
        mock_with_retry.return_value = mock_llm_with_retry

        node = MagicMock(spec=LLMResponse)
        node.get_chat_model = MagicMock(return_value=mock_llm)
        node._config = {}
        node.name = "test"
        node.node_id = "test-id"

        # Call the actual _process method
        state = {"last_node_input": "test input"}
        LLMResponse._process(node, state)

        mock_with_retry.assert_called_once_with(mock_llm)


class TestExtractStructuredDataRetry:
    @patch("apps.pipelines.nodes.mixins.with_llm_retry")
    def test_extraction_chain_uses_retry(self, mock_with_retry):
        """Verify extraction chain wraps LLM with retry."""
        from apps.pipelines.nodes.mixins import ExtractStructuredDataNodeMixin

        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_with_retry.return_value = MagicMock()

        # Create a mock that inherits from ExtractStructuredDataNodeMixin
        class MockNode(ExtractStructuredDataNodeMixin):
            def get_chat_model(self):
                return mock_llm

        node = MockNode()
        node.extraction_chain(MagicMock(), "reference")

        mock_llm.with_structured_output.assert_called_once()
        mock_with_retry.assert_called_once_with(mock_structured)
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/pipelines/nodes/tests/test_retry.py -v`
Expected: FAIL with assertion error about with_llm_retry not called

**Step 3: Modify nodes.py**

```python
# apps/pipelines/nodes/nodes.py
# Add import at top (after line 61):
from apps.service_providers.llm_service.retry import with_llm_retry

# Modify lines 149-152:
    def _process(self, state: PipelineState) -> PipelineState:
        llm = with_llm_retry(self.get_chat_model())
        output = llm.invoke(state["last_node_input"], config=self._config)
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output.content)
```

**Step 4: Modify mixins.py**

```python
# apps/pipelines/nodes/mixins.py
# Add import at top (after line 42):
from apps.service_providers.llm_service.retry import with_llm_retry

# Modify line 353-354:
    def extraction_chain(self, tool_class, reference_data):
        return self._prompt_chain(reference_data) | with_llm_retry(super().get_chat_model().with_structured_output(tool_class))
```

**Step 5: Run test to verify it passes**

Run: `pytest apps/pipelines/nodes/tests/test_retry.py -v`
Expected: PASS

**Step 6: Run existing node tests to ensure no regression**

Run: `pytest apps/pipelines/nodes/tests/ -v -k "not integration"`
Expected: All tests PASS

**Step 7: Lint the files**

Run: `ruff check apps/pipelines/nodes/nodes.py apps/pipelines/nodes/mixins.py --fix && ruff format apps/pipelines/nodes/nodes.py apps/pipelines/nodes/mixins.py`
Expected: Clean output

**Step 8: Commit**

```bash
git add apps/pipelines/nodes/nodes.py apps/pipelines/nodes/mixins.py apps/pipelines/nodes/tests/test_retry.py
git commit -m "feat: add retry logic to pipeline node direct LLM calls"
```

---

## Task 6: Update Evaluators to Include Rate Limit Exceptions

**Files:**
- Modify: `apps/evaluations/evaluators.py:86-89`
- Test: `apps/evaluations/tests/test_evaluator_retry.py`

**Step 1: Write the failing test**

```python
# apps/evaluations/tests/test_evaluator_retry.py
import pytest
from unittest.mock import MagicMock, patch


class TestLlmEvaluatorRetry:
    @patch("apps.evaluations.evaluators.RATE_LIMIT_EXCEPTIONS", (ValueError, Exception))
    def test_retry_includes_rate_limit_exceptions(self):
        """Verify that the evaluator retry includes rate limit exceptions."""
        from apps.evaluations.evaluators import LlmEvaluator, RATE_LIMIT_EXCEPTIONS

        # RATE_LIMIT_EXCEPTIONS should be imported from the retry module
        import openai
        import anthropic
        from google.api_core.exceptions import ResourceExhausted

        assert openai.RateLimitError in RATE_LIMIT_EXCEPTIONS
        assert anthropic.RateLimitError in RATE_LIMIT_EXCEPTIONS
        assert ResourceExhausted in RATE_LIMIT_EXCEPTIONS
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/evaluations/tests/test_evaluator_retry.py -v`
Expected: FAIL with assertion error

**Step 3: Modify evaluators.py**

```python
# apps/evaluations/evaluators.py
# Add import at top (after line 16):
from apps.service_providers.llm_service.retry import RATE_LIMIT_EXCEPTIONS

# Modify lines 84-89:
    def run(self, message: EvaluationMessage, generated_response: str) -> EvaluatorResult:
        # Create a pydantic class so the llm output is validated
        output_model = schema_to_pydantic_model(self.output_schema)
        llm = self.get_chat_model().with_structured_output(output_model)

        llm_with_retry = llm.with_retry(
            stop_after_attempt=3,
            retry_if_exception_type=(ValueError,) + RATE_LIMIT_EXCEPTIONS,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest apps/evaluations/tests/test_evaluator_retry.py -v`
Expected: PASS

**Step 5: Run existing evaluator tests to ensure no regression**

Run: `pytest apps/evaluations/tests/ -v`
Expected: All tests PASS

**Step 6: Lint the file**

Run: `ruff check apps/evaluations/evaluators.py --fix && ruff format apps/evaluations/evaluators.py`
Expected: Clean output

**Step 7: Commit**

```bash
git add apps/evaluations/evaluators.py apps/evaluations/tests/test_evaluator_retry.py
git commit -m "feat: add rate limit exceptions to evaluator retry logic"
```

---

## Task 7: Integration Test and Final Verification

**Files:**
- Test: `apps/service_providers/llm_service/tests/test_retry_integration.py`

**Step 1: Write integration test**

```python
# apps/service_providers/llm_service/tests/test_retry_integration.py
"""
Integration tests to verify retry behavior across the application.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestRetryIntegration:
    """Test that retry is properly configured across all LLM usages."""

    def test_retry_module_imports_correctly(self):
        """Verify the retry module can be imported from all locations."""
        from apps.service_providers.llm_service.retry import (
            RATE_LIMIT_EXCEPTIONS,
            get_retry_policy,
            with_llm_retry,
            should_retry_exception,
        )

        assert RATE_LIMIT_EXCEPTIONS is not None
        assert callable(get_retry_policy)
        assert callable(with_llm_retry)
        assert callable(should_retry_exception)

    def test_retry_policy_configuration(self):
        """Verify retry policy has sensible defaults."""
        from apps.service_providers.llm_service.retry import get_retry_policy

        policy = get_retry_policy()

        assert policy.max_attempts == 3
        assert policy.initial_interval == 1.0
        assert policy.backoff_factor == 2.0
        assert policy.max_interval == 60.0
        assert policy.jitter is True

    def test_graph_imports_retry_policy(self):
        """Verify pipeline graph imports retry policy."""
        from apps.pipelines.graph import get_retry_policy
        assert callable(get_retry_policy)

    def test_analysis_imports_retry(self):
        """Verify analysis modules import retry."""
        from apps.analysis.tasks import with_llm_retry
        from apps.analysis.translation import with_llm_retry as translation_retry

        assert callable(with_llm_retry)
        assert callable(translation_retry)

    def test_evaluators_import_rate_limit_exceptions(self):
        """Verify evaluators import rate limit exceptions."""
        from apps.evaluations.evaluators import RATE_LIMIT_EXCEPTIONS

        import openai
        assert openai.RateLimitError in RATE_LIMIT_EXCEPTIONS
```

**Step 2: Run integration test**

Run: `pytest apps/service_providers/llm_service/tests/test_retry_integration.py -v`
Expected: PASS

**Step 3: Run full test suite for affected modules**

Run: `pytest apps/service_providers/llm_service/tests/ apps/pipelines/tests/ apps/analysis/tests/ apps/evaluations/tests/ apps/experiments/tests/ -v --tb=short`
Expected: All tests PASS

**Step 4: Lint all modified files**

Run: `ruff check apps/service_providers/llm_service/ apps/pipelines/ apps/analysis/ apps/evaluations/ apps/experiments/ --fix`
Expected: Clean output

**Step 5: Commit integration test**

```bash
git add apps/service_providers/llm_service/tests/test_retry_integration.py
git commit -m "test: add integration tests for LLM retry logic"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `apps/service_providers/llm_service/retry.py` | New - centralized retry configuration |
| `apps/pipelines/graph.py` | Add RetryPolicy to all graph nodes |
| `apps/pipelines/nodes/nodes.py` | Add `with_llm_retry()` to LLMResponse |
| `apps/pipelines/nodes/mixins.py` | Add `with_llm_retry()` to extraction chain |
| `apps/analysis/tasks.py` | Add `with_llm_retry()` to transcript analysis |
| `apps/analysis/translation.py` | Add `with_llm_retry()` to translation |
| `apps/experiments/tasks.py` | Add `with_llm_retry()` to prompt builder |
| `apps/evaluations/evaluators.py` | Add rate limit exceptions to existing retry |

## Sources

- [LangChain BaseChatModel with_retry](https://python.langchain.com/api_reference/core/language_models/langchain_core.language_models.chat_models.BaseChatModel.html)
- [LangGraph RetryPolicy Documentation](https://langchain-ai.lang.chat/langgraph/how-tos/node-retries/)
- [LangChain Issue #33515 - with_retry limitation](https://github.com/langchain-ai/langchain/issues/33515)
