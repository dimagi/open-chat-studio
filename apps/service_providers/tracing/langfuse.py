from __future__ import annotations

import atexit
import dataclasses
import logging
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from django.utils import timezone
from langfuse import LangfuseOtelSpanAttributes, propagate_attributes
from langfuse._client.resource_manager import LangfuseResourceManager
from langfuse.langchain import CallbackHandler
from opentelemetry import trace as otel_trace_api

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException, TraceContext

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from uuid import UUID

    from langchain_core.callbacks.base import BaseCallbackHandler
    from langfuse import Langfuse
    from langfuse.api.client import LangfuseAPI

    from apps.experiments.models import ExperimentSession

    from .const import SpanLevel


logger = logging.getLogger("ocs.tracing.langfuse")


def normalize_sample_rate(sample_rate: float | None) -> float | None:
    """Normalize a Langfuse sample rate.

    Returns ``None`` when the rate is exactly ``0.0`` ("trace nothing"): langfuse's
    ``Langfuse.__init__`` treats ``sample_rate=0.0`` as falsy and silently substitutes the
    ``LANGFUSE_SAMPLE_RATE`` env var or 1.0, so "trace nothing" has to be enforced here
    rather than trusted to the SDK. A blank rate is normalized to ``1.0`` for the same
    reason: passing ``None`` through lets the SDK fall back to that env var if one happens
    to be set, silently overriding "leave blank to trace every call".

    Raises ``ValueError`` outside ``0.0``-``1.0``, matching the SDK's own validation: an
    out-of-range rate would otherwise reach ``Langfuse.__init__`` and raise the identical
    error there, deeper in client construction.
    """
    if sample_rate == 0.0:
        return None
    if sample_rate is None:
        return 1.0
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError(f"Sample rate must be between 0.0 and 1.0, got {sample_rate}")
    return sample_rate


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
        self._root_otel_span: otel_trace_api.Span | None = None
        self._trace_tags: list[str] = []

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
                    self._root_otel_span = otel_trace_api.get_current_span()
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
            self._root_otel_span = None
            self._trace_tags = []
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
            "dict[str, str]",
            {
                "trace_id": self._langfuse_trace_id,
                "trace_url": trace_url,
                "trace_provider": self.type,
            },
        )

    def add_trace_tags(self, tags: list[str]) -> None:
        """Add tags to the trace.

        Langfuse reads trace-level attributes off the root observation, and ``span.update()``
        does not accept tags. Tags arrive mid-trace, from the output message, when a nested
        span is current, so they go straight onto the root span: the same attribute
        ``propagate_attributes`` writes, on the span Langfuse reads it from.

        Each call rewrites the attribute with the accumulated set, since a later call would
        otherwise replace earlier tags.
        """
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        new_tags = [tag for tag in tags if tag not in self._trace_tags]
        if not new_tags:
            return
        self._trace_tags.extend(new_tags)

        if self._root_otel_span is not None and self._root_otel_span.is_recording():
            self._root_otel_span.set_attribute(LangfuseOtelSpanAttributes.TRACE_TAGS, tuple(self._trace_tags))

    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    def set_input_message_id(self, input_message_id: str) -> None:
        pass

    def set_participant_data_diff(self, diff: list[tuple[str, str | list, Any]]) -> None:
        pass


def _detach_sdk_resources(public_key: str) -> LangfuseResourceManager | None:
    """Unregister the SDK's cached resources for ``public_key`` and return them.

    ``LangfuseResourceManager`` is a process-wide singleton keyed by public_key alone and the
    SDK gives no way to retire one: ``Langfuse.shutdown()`` stops the consumer threads but
    leaves the instance registered, so the next ``Langfuse(**config)`` hands back a manager
    that will never flush again. Removing the registry entry is therefore what makes a
    credential or sample-rate change take effect, and what releases an idle team's threads.

    The returned instance still owns live threads. Shutting it down is left to the caller,
    via ``_shutdown_detached``, so it can happen outside ``ClientManager._lock``.

    This is the only place OCS touches Langfuse internals; ``test_sdk_registry_seam_exists``
    fails if either attribute goes away.
    """
    with LangfuseResourceManager._lock:
        return LangfuseResourceManager._instances.pop(public_key, None)


def _shutdown_detached(instances: list[LangfuseResourceManager]) -> None:
    """Shut down instances that ``_detach_sdk_resources`` has already unregistered.

    ``shutdown()`` flushes and joins the instance's queues, so it can block for as long as
    the flush takes and, if a consumer thread has died, indefinitely. Callers run it with no
    lock of theirs held, so one team's stuck flush cannot stall every other team's ``get()``.
    Detaching first is what makes that safe: the instance is already out of the registry, so
    a concurrent ``get()`` for the same public_key builds a fresh one rather than waiting.
    """
    for instance in instances:
        try:
            instance.shutdown()
        except Exception:
            logger.exception("Failed to shut down Langfuse client resources")


@dataclasses.dataclass
class _CachedClient:
    client: Langfuse
    public_key: str | None
    last_used: float = 0.0


class ClientManager:
    """Caches one Langfuse client per trace provider config, and retires idle ones.

    The SDK already caches the expensive per-key state — exporter, consumer threads, httpx
    client — on its own ``LangfuseResourceManager`` singleton, so this class exists for the
    two things that singleton does not do:

    * it is keyed by public_key alone, so a rotated secret or a changed sample_rate for a
      public_key already seen would be silently ignored. Caching by config hash and evicting
      the SDK's entry on a miss makes the new config take effect.
    * it never releases a key, so every team that has ever traced in this process keeps its
      threads and connections. Pruning by last use and by ``max_clients`` bounds that.
    """

    def __init__(self, stale_timeout=300, prune_interval=60, max_clients=20) -> None:
        self._lock = threading.RLock()
        self._entries: dict[int, _CachedClient] = {}
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
        detached: list[LangfuseResourceManager] = []
        with self._lock:
            entry = self._entries.get(config_hash)
            if entry is None:
                detached = self._evict_public_key(public_key, keep=config_hash)
                logger.debug("Creating new Langfuse client with public_key '%s'", public_key)
                entry = _CachedClient(client=Langfuse(**config), public_key=public_key)
                self._entries[config_hash] = entry
            entry.last_used = time.time()
            client = entry.client
        _shutdown_detached(detached)
        return client

    def _evict_public_key(self, public_key, keep: int) -> list[LangfuseResourceManager]:
        """Remove any entry cached under a different config hash for this public_key.

        Without this, a config change for a public_key we've already seen would still find
        the SDK's stale singleton for that key and silently reuse it.
        """
        return self._remove_clients(
            other_hash
            for other_hash, other_entry in list(self._entries.items())
            if other_hash != keep and other_entry.public_key == public_key
        )

    def _start_prune_thread(self):
        self._prune_thread = threading.Thread(target=self._prune_worker, daemon=True)
        self._prune_thread.start()

    def _prune_worker(self):
        while True:
            time.sleep(self.prune_interval)
            self._prune_stale()

    def _prune_stale(self):
        detached: list[LangfuseResourceManager] = []
        with self._lock:
            if self._entries:
                logger.debug("Pruning clients...")
                now = time.time()
                stale = [h for h, entry in self._entries.items() if now - entry.last_used > self.stale_timeout]
                if stale:
                    logger.debug("Pruning %d stale clients", len(stale))
                    detached += self._remove_clients(stale)

                if len(self._entries) > self.max_clients:
                    # remove the oldest clients until we are below the max
                    by_age = sorted(self._entries, key=lambda h: self._entries[h].last_used)
                    over_limit = by_age[: len(self._entries) - self.max_clients]
                    logger.debug("Pruned %d clients above max limit", len(over_limit))
                    detached += self._remove_clients(over_limit)
        _shutdown_detached(detached)

    def _remove_clients(self, config_hashes: Iterable[int]) -> list[LangfuseResourceManager]:
        """Drop these cache entries and return the SDK resources the caller must shut down.

        Callers hold `self._lock`, so a background prune can't interleave with a concurrent
        `get()` and leave `_entries` inconsistent with the SDK's registry. They shut the
        returned instances down once they have released that lock.
        """
        entries = [self._entries.pop(config_hash, None) for config_hash in config_hashes]
        detached = [
            _detach_sdk_resources(entry.public_key)
            for entry in entries
            if entry is not None and entry.public_key is not None
        ]
        return [instance for instance in detached if instance is not None]

    def shutdown(self):
        with self._lock:
            if self._entries:
                logger.debug("Shutting down all langfuse clients (%s)", len(self._entries))
            LangfuseResourceManager.reset()
            self._entries.clear()


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
        # LangChain's run_id -> observation map is internal to the handler and there is no
        # public accessor, so a custom event can only be parented via the private lookup.
        # ``test_langchain_parent_observation_seam_exists`` fails if it goes away.
        if span := self._get_parent_observation(run_id):
            span.create_event(name=name, input=data, metadata=metadata)
        return None
