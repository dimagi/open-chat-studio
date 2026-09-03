from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.tracing import get_help_agent_tracer
from apps.service_providers.tracing.base import TraceContext

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("ocs.help")


class BaseHelpAgent[TInput: BaseModel, TOutput: BaseModel](BaseModel):
    """Base class for help agents.

    Subclasses must define:
    - name: ClassVar[str] — registry key and URL slug
    - mode: ClassVar[Literal["high", "low"]] — model tier

    Subclasses that use the default run() must also define:
    - get_system_prompt(input) — build the system prompt
    - get_user_message(input) — build the user message

    Subclasses may optionally override:
    - parse_response(response) — custom response extraction (default: return structured_response)
    - run() — entirely custom execution logic

    The default run() passes TOutput as response_format for structured output.
    """

    name: ClassVar[str]
    mode: ClassVar[Literal["high", "low"]]

    input: TInput

    @classmethod
    def _get_output_type(cls) -> type[BaseModel]:
        """Resolve the concrete TOutput type from the class hierarchy."""
        for klass in cls.__mro__:
            meta = getattr(klass, "__pydantic_generic_metadata__", None)
            if meta and meta.get("origin") is BaseHelpAgent:
                return meta["args"][1]
        raise TypeError(f"Cannot determine output type for {cls.__name__}")

    @classmethod
    def get_system_prompt(cls, input: TInput) -> str:
        raise NotImplementedError

    @classmethod
    def get_user_message(cls, input: TInput) -> str:
        raise NotImplementedError

    @contextmanager
    def _trace(self, inputs: dict) -> Iterator[RunnableConfig]:
        """Wrap one help-agent invocation in a Langfuse trace, if the operator has
        configured one (see apps/help/tracing.py). Yields a RunnableConfig carrying the
        tracing callback for agent.invoke(..., config=...); yields an empty config when
        no tracer is configured, so callers behave exactly as before tracing existed.

        A synchronous failure while creating the trace (a malformed config, an SDK-internal
        exception) is logged and falls back to an empty config rather than propagating.
        Mirrors TracingService.trace(). This does not need to cover an unreachable Langfuse
        host or bad credentials specifically: neither the SDK client nor
        start_as_current_observation() connects eagerly, so those surface later, during
        LangFuseTracer.trace()'s own flush() in its finally block, where OpenTelemetry's
        exporter already logs and swallows the failure rather than raising it. Verified by
        pointing LANGFUSE_HOST at an unresolvable domain and exercising this code path in a
        real browser session: the request completed normally, and the export failure showed
        up only as an "opentelemetry.sdk._shared_internal" log line, never reaching here.
        get_help_agent_tracer() itself is covered too, in its own try/except that contains
        no yield: an out-of-range LANGFUSE_SAMPLE_RATE raises there (see
        normalize_sample_rate), and it must not break the request either. Keeping that
        outside the trace-entry try/except matters: a yield inside a try/except can receive
        an exception thrown back in from the caller's own code (e.g. a real bug in
        agent.invoke()) when the `with` block exits, and that must propagate untouched, not
        get misreported as a tracing failure.
        """
        from langchain_core.runnables import RunnableConfig  # noqa: PLC0415 - lazy: avoids loading langchain_core

        try:
            tracer = get_help_agent_tracer()
        except Exception:
            logger.exception("Failed to build Langfuse tracer for help_agent:%s; continuing untraced", self.name)
            tracer = None

        if tracer is None:
            yield RunnableConfig()
            return

        team_id = getattr(self.input, "team_id", None)
        metadata = {"team_id": str(team_id)} if team_id is not None else None
        trace_context = TraceContext(id=uuid.uuid4(), name=f"help_agent:{self.name}")

        with ExitStack() as stack:
            try:
                stack.enter_context(tracer.trace(trace_context, session=None, inputs=inputs, metadata=metadata))
                callback = tracer.get_langchain_callback()
            except Exception:
                logger.exception("Failed to start Langfuse trace for help_agent:%s; continuing untraced", self.name)
                yield RunnableConfig()
                return
            yield RunnableConfig(callbacks=[callback] if callback else [])

    def run(self) -> TOutput:
        with self._trace({"query": self.get_user_message(self.input)}) as trace_config:
            agent = build_system_agent(
                self.mode,
                self.get_system_prompt(self.input),
                response_format=self._get_output_type(),
            )
            response = agent.invoke(
                {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]},
                config=trace_config,
            )
        return self.parse_response(response)

    def parse_response(self, response) -> TOutput:
        return response["structured_response"]
