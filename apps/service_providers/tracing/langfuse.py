from __future__ import annotations

import atexit
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from django.utils import timezone
from langfuse import propagate_attributes
from langfuse._client.get_client import _create_client_from_instance
from langfuse._client.resource_manager import LangfuseResourceManager
from langfuse.langchain import CallbackHandler

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException, TraceContext
from .const import SpanLevel

if TYPE_CHECKING:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langfuse import Langfuse
    from langfuse.api.client import LangfuseAPI

    from apps.experiments.models import ExperimentSession


logger = logging.getLogger("ocs.tracing.langfuse")


def get_langfuse_api_client(config: dict) -> LangfuseAPI:
    """Create a Langfuse management API client for reading trace data."""
    from langfuse.api.client import LangfuseAPI  # noqa: PLC0415 - tests mock LangfuseAPI at source module

    return LangfuseAPI(
        base_url=config["host"],
        username=config["public_key"],
        password=config["secret_key"],
        timeout=10,
    )


def fetch_project_metadata(config: dict) -> dict:
    """Fetch the Langfuse project and organization that ``config``'s API keys belong to.

    ``GET /api/public/projects`` resolves the project from the key pair itself, so a
    project-scoped key returns exactly one project. An organization-scoped key can
    return several, in which case we have nothing to disambiguate on and record none
    of them rather than attributing usage to an arbitrary project.

    Raises whatever the Langfuse client raises (network, auth, etc.); callers decide
    how loud that failure should be.
    """
    projects = get_langfuse_api_client(config).projects.get().data
    if len(projects) != 1:
        raise ValueError(f"Expected exactly one Langfuse project for these credentials, got {len(projects)}")

    project = projects[0]
    return {
        "project_id": project.id,
        "project_name": project.name,
        "organization_id": project.organization.id,
        "organization_name": project.organization.name,
        "retention_days": project.retention_days,
        "synced_at": timezone.now().isoformat(),
    }


class LangFuseTracer(Tracer):
    """
    Notes on langfuse:

    The API is designed to be used with a single set of credentials whereas we need to provide
    different credentials per call. This is why we don't use the standard 'observe' decorator.

    Error propagation: Langfuse's UI surfaces span failures via its own ``level`` field, which
    is independent of OpenTelemetry status. The SDK only maps one direction (``level=ERROR``
    sets OTel status), so a propagating exception leaves OTel status=ERROR but ``level``
    unset — the span renders as successful. ``span()`` and ``trace()`` therefore catch the
    exception, mark the ``TraceContext``, and run ``_update_span_from_context`` in a
    ``finally`` so ``level=ERROR`` is set before the underlying observation closes.
    """

    def __init__(self, type_: str, config: dict):
        super().__init__(type_, config)
        self.client = None
        self.trace_record = None
        self._langfuse_trace_id: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.trace_record)

    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession | None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for Langfuse trace lifecycle.

        Acquires a Langfuse client from ClientManager, creates a trace,
        and ensures the client is flushed on exit.

        ``session`` may be None when the trace is opened before routing has
        identified a session (e.g. inbound email). Langfuse cannot back-fill
        ``session_id``/``user_id`` after the trace is sent, so they are
        omitted in that case.
        """
        # Check for reentry
        if self.trace_record:
            raise ServiceReentryException("Service does not support reentrant use.")

        self.session = session

        # Get client and create trace
        self.client = client_manager.get(self.config)
        propagate_kwargs: dict[str, str] = {}
        if session is not None:
            propagate_kwargs["session_id"] = str(session.external_id)
            propagate_kwargs["user_id"] = session.participant.identifier
        try:
            with propagate_attributes(**propagate_kwargs):
                with self.client.start_as_current_observation(
                    name=trace_context.name,
                    input=inputs,
                    metadata=metadata,
                ) as trace:
                    self.trace_record = trace
                    self._langfuse_trace_id = self.client.get_current_trace_id()
                    try:
                        yield trace_context
                    except Exception as exc:
                        if not trace_context.has_error():
                            trace_context.mark_span_as_error(str(exc), exception=exc)
                        raise
                    finally:
                        self._update_span_from_context(trace, trace_context)
        finally:
            if self.trace_record:
                self.client.flush()

            # Reset state
            self.client = None
            self.trace_record = None
            self._langfuse_trace_id = None
            self.session = None

    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """Context manager for Langfuse span lifecycle.

        Creates a nested span under the current observation (last span or root trace).
        """
        if not self.ready:
            yield span_context
            return

        with self.client.start_as_current_observation(
            name=span_context.name,
            input=inputs,
            metadata=metadata,
            level=level,
        ) as span:
            try:
                yield span_context
            except Exception as exc:
                if not span_context.has_error():
                    span_context.mark_span_as_error(str(exc), exception=exc)
                raise
            finally:
                self._update_span_from_context(span, span_context)

    def _update_span_from_context(self, span, context: TraceContext):
        # Best-effort: this is called from `finally` blocks, so a failure here would
        # replace any in-flight application exception and hide the real failure.
        try:
            if output := context.outputs:
                span.update(output=output.copy())

            if exc := context.exception:
                span.update(level="ERROR", status_message=str(exc))

            if error := context.error:
                span.update(level="ERROR", status_message=error)
        except Exception:
            logger.exception("Failed to update Langfuse span state for span %s", context.name)

    def get_langchain_callback(self) -> BaseCallbackHandler | None:  # ty: ignore[invalid-method-override]
        if not self.ready:
            raise ServiceReentryException("Service does not support reentrant use.")

        if self.config and self.config.get("public_key"):
            public_key = self.config.get("public_key")
            return LangfuseCallbackHandler(public_key=public_key)
        return None

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        # get_trace_url() does a blocking HTTP fetch for project_id; isolate failures
        # so we still capture trace_id in the chat message metadata.
        try:
            trace_url = self.client.get_trace_url(trace_id=self._langfuse_trace_id)
        except Exception:
            logger.exception("Failed to fetch Langfuse trace URL for trace_id=%s", self._langfuse_trace_id)
            trace_url = None

        return cast(
            dict[str, str],
            {
                "trace_id": self._langfuse_trace_id,
                "trace_url": trace_url,
                "trace_provider": self.type,
            },
        )

    def add_trace_tags(self, tags: list[str]) -> None:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")
        # span.update() no longer accepts tags in v4; use the ingestion API directly
        self.client._create_trace_tags_via_ingestion(trace_id=self._langfuse_trace_id, tags=tags)

    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    def set_input_message_id(self, input_message_id: str) -> None:
        pass

    def set_participant_data_diff(self, diff: list[tuple[str, str | list, Any]]) -> None:
        pass


class ClientManager:
    """This class manages the langfuse clients to avoid creating a new client for every request.
    On requests for a client it will also remove any clients that have been inactive for a
    certain amount of time.

    Cached per config hash rather than public_key alone, so any config change (a rotated key, a
    changed sample_rate, ...) misses the cache and builds a fresh client instead of needing a
    separate invalidation path. `LangfuseResourceManager` -- the SDK's own client cache -- is
    still keyed by public_key only, so a config change for a public_key already cached here
    evicts that stale SDK-level entry too, rather than leaving it to the next stale-prune pass.
    """

    def __init__(self, stale_timeout=300, prune_interval=60, max_clients=20) -> None:
        self.key_timestamps: dict[int, float] = {}
        self._public_keys: dict[int, str | None] = {}
        self.stale_timeout = stale_timeout
        self.max_clients = max_clients
        self.prune_interval = prune_interval
        self._start_prune_thread()

    @staticmethod
    def _config_hash(config: dict) -> int:
        return hash(frozenset(config.items()))

    def get(self, config: dict) -> Langfuse:
        from langfuse import Langfuse  # noqa: PLC0415 - tests mock langfuse.Langfuse at source module

        public_key = config.get("public_key")
        config_hash = self._config_hash(config)
        with LangfuseResourceManager._lock:
            if config_hash not in self.key_timestamps:
                self._evict_public_key(public_key, keep=config_hash)

            active_instances = LangfuseResourceManager._instances
            if target_instance := active_instances.get(public_key, None):
                client = _create_client_from_instance(target_instance, public_key)
            else:
                logger.debug("Creating new Langfuse client with public_key '%s'", public_key)
                client = Langfuse(**config)
            self.key_timestamps[config_hash] = time.time()
            self._public_keys[config_hash] = public_key
        return client

    def _evict_public_key(self, public_key, keep: int) -> None:
        """Remove any entry cached under a different config hash for this public_key.

        Without this, a config change for a public_key we've already seen would still find
        `LangfuseResourceManager`'s stale singleton for that key and silently reuse it.
        """
        for other_hash, other_key in list(self._public_keys.items()):
            if other_hash != keep and other_key == public_key:
                self._remove_client(other_hash)

    def _start_prune_thread(self):
        self._prune_thread = threading.Thread(target=self._prune_worker, daemon=True)
        self._prune_thread.start()

    def _prune_worker(self):
        while True:
            time.sleep(self.prune_interval)
            self._prune_stale()

    def _prune_stale(self):
        if not self.key_timestamps:
            return

        logger.debug("Pruning clients...")
        for config_hash in list(self.key_timestamps.keys()):
            timestamp = self.key_timestamps[config_hash]
            if time.time() - timestamp > self.stale_timeout:
                logger.debug("Pruning old client for public_key '%s'", self._public_keys.get(config_hash))
                self._remove_client(config_hash)

        if len(self.key_timestamps) > self.max_clients:
            # remove the oldest clients until we are below the max
            sorted_keys = sorted(self.key_timestamps.items(), key=lambda x: x[1])
            keys_to_remove = sorted_keys[: len(self.key_timestamps) - self.max_clients]
            logger.debug("Pruned %d clients above max limit", len(keys_to_remove))
            for config_hash, _ in keys_to_remove:
                self._remove_client(config_hash)

    def _remove_client(self, config_hash: int):
        # All bookkeeping happens under the same lock `get()` holds for its whole body, so a
        # background prune (this method's other caller, from `_prune_worker`'s thread) can't
        # interleave with a concurrent `get()` and leave the two dicts and
        # `LangfuseResourceManager._instances` inconsistent with each other.
        with LangfuseResourceManager._lock:
            public_key = self._public_keys.pop(config_hash, None)
            self.key_timestamps.pop(config_hash, None)
            if public_key is None:
                return
            active_instances = LangfuseResourceManager._instances
            if target_instance := active_instances.pop(public_key, None):
                target_instance.shutdown()

    def shutdown(self):
        if self.key_timestamps:
            logger.debug("Shutting down all langfuse clients (%s)", len(self.key_timestamps))
        with LangfuseResourceManager._lock:
            LangfuseResourceManager.reset()
            self.key_timestamps.clear()
            self._public_keys.clear()


client_manager = ClientManager()


@atexit.register
def _shutdown():
    """Shutdown the client manager when the program exits."""
    client_manager.shutdown()


class LangfuseCallbackHandler(CallbackHandler):
    """Langfuse callback handler for LangChain that supports custom events"""

    def on_custom_event(
        self,
        name: str,
        data: Any,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if span := self._get_parent_observation(run_id):
            span.create_event(name=name, input=data, metadata=metadata)
        return None
