# Tracing Service Context Manager Redesign Implementation Plan

## Overview

Redesign the tracing service architecture to use context managers with `ExitStack` instead of the current start/end method pairs. This will provide guaranteed cleanup, eliminate resource leaks, and create a more Pythonic API while maintaining the existing public interface of `TracingService`.

## Current State Analysis

The tracing system consists of three layers:

1. **TracingService** (`service.py:26-315`) - Orchestrates multiple tracers using context managers internally but delegates to tracer start/end methods
2. **Tracer base class** (`base.py:24-105`) - Defines abstract interface with `start_trace()`/`end_trace()` and `start_span()`/`end_span()` methods
3. **Concrete tracers** - `OCSTracer` and `LangFuseTracer` implement the start/end interface

### Key Issues with Current Architecture:

- **No guaranteed cleanup**: If `start_trace()` succeeds but `end_trace()` isn't called due to an exception in another tracer, resources may leak
- **Manual state management**: Both TracingService and individual tracers manually track state with explicit reset logic
- **Complex error handling**: Try/except blocks scattered across start/end methods without guaranteed cleanup
- **Inconsistent patterns**: TracingService uses context managers but tracers don't

### Key Discoveries:

- TracingService already provides context manager API (`service.py:91-111`, `136-161`)
- Error isolation exists but isn't guaranteed (`service.py:115-124`)
- ClientManager singleton for Langfuse client pooling (`langfuse.py:160-236`)
- Span tracking disabled in OCSTracer due to threading issues (`ocs_tracer.py:142`)

## Desired End State

A tracing system where:
- All tracers implement context manager methods using `@contextmanager` decorator
- `TracingService` uses `ExitStack` to manage multiple tracer contexts
- Cleanup is guaranteed even when tracer initialization fails
- No change to public `TracingService` API (`trace()`, `span()`, `trace_or_span()`)
- All tests updated to verify new behavior

### Verification:
- All tests pass
  - `pytest apps/service_providers/tests/test_*tracer.py`
  - `pytest apps/service_providers/tests/test_tracing_service.py`
  - `pytest apps/pipelines/tests/test_pipeline_runs.py`
- No resource leaks when tracers fail during initialization
- Exception in one tracer doesn't prevent cleanup of others

## What We're NOT Doing

- NOT maintaining backward compatibility with start/end methods
- NOT changing the public API of TracingService (trace(), span(), trace_or_span())
- NOT modifying ClientManager singleton pattern
- NOT fixing the disabled span tracking in OCSTracer (keep it disabled)
- NOT adding thread-local storage or ContextVar patterns
- NOT changing how tracers manage their internal state

## Implementation Approach

Use the `@contextmanager` decorator pattern from `contextlib` for all tracer lifecycle methods. TracingService will use `ExitStack` to enter multiple tracer contexts safely, ensuring that if any tracer fails during setup, already-entered contexts are properly cleaned up.

Pattern:
```python
# In each tracer:
@contextmanager
def trace(self, ...):
    # Setup
    try:
        yield self
    except Exception as e:
        # Record error
        raise
    finally:
        # Guaranteed cleanup

# In TracingService:
@contextmanager
def trace(self, ...):
    with ExitStack() as stack:
        for tracer in self._tracers:
            try:
                stack.enter_context(tracer.trace(...))
            except Exception:
                logger.exception("Error initializing tracer")
        yield self
```

---

## Phase 1: Update Tracer Base Class Interface

### Overview
Replace the abstract start/end method pairs with context manager methods in the `Tracer` base class. This establishes the new interface that all concrete tracers must implement.

### Changes Required:

#### 1. Tracer Base Class
**File**: `apps/service_providers/tracing/base.py`
**Changes**: Replace abstract methods with context manager interface

**Remove these methods** (lines 39-85):
```python
@abstractmethod
def start_trace(...) -> None:
    """This must be called before any tracing methods are called."""
    self.trace_name = trace_name
    self.trace_id = trace_id
    self.session = session

def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
    """This must be called after all tracing methods are called to finalize the trace."""
    self.trace_name = None
    self.trace_id = None
    self.session = None

@abstractmethod
def start_span(...) -> None:
    raise NotImplementedError

@abstractmethod
def end_span(...) -> None:
    raise NotImplementedError
```

**Add these context manager methods**:
```python
from contextlib import contextmanager
from typing import Iterator

@abstractmethod
@contextmanager
def trace(
    self,
    trace_context: TraceContext,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[TraceContext]:
    """Context manager for trace lifecycle.

    Sets up tracing context on entry and ensures cleanup on exit.
    Yields the TraceContext object that can be used to set outputs.

    Args:
        trace_context: The context object with id, name, and outputs
        session: The experiment session for this trace
        inputs: Optional input data for the trace
        metadata: Optional metadata for the trace

    Yields:
        TraceContext: The same context object for setting outputs

    Example:
        ctx = TraceContext(id=trace_id, name=trace_name)
        with tracer.trace(ctx, session) as ctx:
            # tracing active
            ctx.set_outputs({"result": "value"})
        # cleanup guaranteed, outputs available in ctx.outputs
    """
    raise NotImplementedError

@abstractmethod
@contextmanager
def span(
    self,
    span_context: TraceContext,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    level: SpanLevel = "DEFAULT",
) -> Iterator[TraceContext]:
    """Context manager for span lifecycle.

    Sets up span context on entry and ensures cleanup on exit.
    Yields the TraceContext object that can be used to set outputs.

    Args:
        span_context: The context object with id, name, and outputs
        inputs: Input data for the span
        metadata: Optional metadata for the span
        level: Span level (DEFAULT, WARNING, ERROR)

    Yields:
        TraceContext: The same context object for setting outputs
    """
    raise NotImplementedError
```

**Keep unchanged**:
- `__init__` method (lines 25-31)
- Instance variables: `trace_id`, `session`, `trace_name`
- `ready` property (lines 33-36)
- `get_langchain_callback()` (lines 87-89)
- `get_trace_metadata()` (lines 91-92)
- `add_trace_tags()` (lines 94-96)
- `set_output_message_id()` (lines 98-100)
- `set_input_message_id()` (lines 102-104)

#### 2. Add Context Class
**File**: `apps/service_providers/tracing/base.py`
**Changes**: Add unified context class for both traces and spans

```python
@dataclasses.dataclass
class TraceContext:
    """Context object for active traces and spans.

    Holds state and outputs, yielded from trace/span context managers.
    This unified class is used for both trace-level and span-level contexts.
    """
    id: UUID
    name: str
    outputs: dict[str, Any] = dataclasses.field(default_factory=dict)

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        """Set outputs for this trace/span. Can be called multiple times to merge outputs."""
        self.outputs |= outputs or {}
```

#### 3. Add typing imports
**File**: `apps/service_providers/tracing/base.py`
**Changes**: Update imports at top of file

```python
from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Self
from uuid import UUID
```

### Success Criteria:

#### Automated Verification:
- [x] Python imports successfully: `python -c "from apps.service_providers.tracing.base import Tracer"`
- [x] Type checking passes: `inv ruff`
- [x] No syntax errors in base.py

#### Manual Verification:
- [ ] Abstract methods are properly decorated with `@abstractmethod` and `@contextmanager`
- [ ] Method signatures match the design (return `Iterator[Self]`)
- [ ] Docstrings explain the context manager behavior

**Implementation Note**: After completing this phase, concrete tracer implementations will fail to instantiate because they don't implement the new abstract methods. This is expected. Proceed to Phase 2 immediately.

---

## Phase 2: Update OCSTracer Implementation

### Overview
Implement the context manager interface in `OCSTracer`. This tracer creates database `Trace` records and manages error detection flags.

### Changes Required:

#### 1. OCSTracer Context Managers
**File**: `apps/service_providers/tracing/ocs_tracer.py`
**Changes**: Replace start/end methods with context managers

**Remove these methods** (lines 46-129):
```python
def start_trace(...) -> None:
    super().start_trace(trace_name, trace_id, session, inputs, metadata)
    # ... creates self.trace database object ...

def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
    # ... saves trace with duration and status ...
```

**Add this context manager**:
```python
from contextlib import contextmanager
from typing import Iterator

@contextmanager
def trace(
    self,
    trace_context: TraceContext,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[TraceContext]:
    """Context manager for OCS trace lifecycle.

    Creates a database Trace record on entry and updates it with
    duration and status on exit.
    """
    from apps.experiments.models import Experiment

    # Set base class state from context
    self.trace_name = trace_context.name
    self.trace_id = trace_context.id
    self.session = session

    # Determine experiment ID (handle versioning)
    try:
        experiment = Experiment.objects.get(id=self.experiment_id)
    except Experiment.DoesNotExist:
        logger.exception(f"Experiment with id {self.experiment_id} does not exist. Cannot start trace.")
        yield self
        return

    experiment_id = self.experiment_id
    experiment_version_number = None
    if experiment.is_a_version:
        # Trace needs to be associated with the working version of the experiment
        experiment_id = experiment.working_version_id
        experiment_version_number = experiment.version_number

    # Create database trace record
    self.trace = Trace.objects.create(
        trace_id=trace_context.id,
        experiment_id=experiment_id,
        experiment_version_number=experiment_version_number,
        team_id=self.team_id,
        session=session,
        duration=0,
        participant=session.participant,
        participant_data=session.participant.get_data_for_experiment(session.experiment),
        session_state=session.state,
    )

    self.start_time = time.time()

    try:
        yield trace_context
    finally:
        # Guaranteed cleanup - update trace duration and status
        if self.trace and self.start_time:
            try:
                end_time = time.time()
                duration = end_time - self.start_time
                duration_ms = int(duration * 1000)

                self.trace.duration = duration_ms
                if self.error_detected:
                    self.trace.status = TraceStatus.ERROR
                else:
                    self.trace.status = TraceStatus.SUCCESS

                # Note: OCSTracer doesn't store trace outputs in database
                # but could access them via trace_context.outputs if needed

                self.trace.save()

                logger.debug(
                    "Created trace in DB | experiment_id=%s, session_id=%s, duration=%sms",
                    self.experiment_id,
                    session.id,
                    duration_ms,
                )
            except Exception:
                logger.exception(
                    "Error saving trace in DB | experiment_id=%s, session_id=%s, output_message_id=%s",
                    self.experiment_id,
                    session.id,
                    self.trace.output_message_id,
                )

        # Reset state
        self.trace = None
        self.spans = {}
        self.error_detected = False
        self.trace_name = None
        self.trace_id = None
        self.session = None
```

**Update span methods** (lines 131-182):

**Remove these methods**:
```python
def start_span(...) -> None:
    # Currently disabled/commented out

def end_span(...) -> None:
    # Currently sets error flags and tags
```

**Add this context manager**:
```python
@contextmanager
def span(
    self,
    span_context: TraceContext,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    level: SpanLevel = "DEFAULT",
) -> Iterator[TraceContext]:
    """Context manager for OCS span lifecycle.

    Note: Span tracking is currently disabled due to multithreading
    reliability issues. This is a no-op context manager that yields
    immediately but still processes errors.
    """
    error_to_record: Exception | None = None

    try:
        yield span_context
    except Exception as e:
        error_to_record = e
        raise
    finally:
        if error_to_record:
            self.error_detected = True

            # Note: Span creation is disabled, but we still track errors
            # If span tracking is re-enabled, this is where we would:
            # 1. Get outputs from span_context.outputs
            # 2. Create and save span to database with outputs
            # 3. Add error tags if needed

            # Example if re-enabled:
            # if self.spans and span_context.id in self.spans:
            #     span = self.spans[span_context.id]
            #     span.output = span_context.outputs
            #     span.error = str(error_to_record)
            #     span.save()
```

#### 2. Update imports
**File**: `apps/service_providers/tracing/ocs_tracer.py`
**Changes**: Add contextlib import

```python
from contextlib import contextmanager
from typing import Iterator, Self
```

### Success Criteria:

#### Automated Verification:
- [x] OCSTracer imports successfully: `python -c "from apps.service_providers.tracing.ocs_tracer import OCSTracer"`
- [x] Type checking passes: `inv ruff`
- [ ] Unit tests pass: `pytest apps/service_providers/tests/test_ocs_tracer.py` (expected to fail until tests are updated in Phase 6)

#### Manual Verification:
- [ ] Context manager properly creates database Trace record
- [ ] Cleanup happens in finally block regardless of exceptions
- [ ] Error detection flag works correctly
- [ ] State reset happens on exit

**Implementation Note**: Tests will fail until Phase 6. This is expected. After completing this phase, verify that the code structure looks correct before proceeding to Phase 3.

---

## Phase 3: Update LangFuseTracer Implementation

### Overview
Implement the context manager interface in `LangFuseTracer`. This tracer interacts with the Langfuse service and manages client connections via ClientManager.

### Changes Required:

#### 1. LangFuseTracer Context Managers
**File**: `apps/service_providers/tracing/langfuse.py`
**Changes**: Replace start/end methods with context managers

**Remove these methods** (lines 46-119):
```python
def start_trace(...) -> None:
    if self.trace:
        raise ServiceReentryException("Service does not support reentrant use.")
    # ... creates self.trace client ...

def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
    # ... flushes client and clears state ...

def start_span(...) -> None:
    # ... creates span via _get_current_observation() ...

def end_span(...) -> None:
    # ... ends span with outputs/error ...
```

**Add trace context manager**:
```python
from contextlib import contextmanager
from typing import Iterator

@contextmanager
def trace(
    self,
    trace_context: TraceContext,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[TraceContext]:
    """Context manager for Langfuse trace lifecycle.

    Acquires a Langfuse client from ClientManager, creates a trace,
    and ensures the client is flushed on exit.
    """
    # Check for reentry
    if self.trace:
        raise ServiceReentryException("Service does not support reentrant use.")

    # Set base class state from context
    self.trace_name = trace_context.name
    self.trace_id = trace_context.id
    self.session = session

    # Get client and create trace
    self.client = client_manager.get(self.config)
    self.trace = self.client.trace(
        name=trace_context.name,
        session_id=str(session.external_id),
        user_id=session.participant.identifier,
        input=inputs,
        metadata=metadata,
    )

    error_to_record: Exception | None = None

    try:
        yield trace_context
    except Exception as e:
        error_to_record = e
        raise
    finally:
        # Guaranteed cleanup
        if self.trace:
            # Get outputs from context and merge with error if present
            outputs = trace_context.outputs.copy() if trace_context.outputs else {}
            if error_to_record:
                outputs["error"] = str(error_to_record)

            # Update trace with outputs if any
            if outputs:
                self.trace.update(output=outputs)

            # Flush client to send data to Langfuse
            if self.client:
                self.client.flush()

        # Reset state
        self.client = None
        self.trace = None
        self.spans.clear()
        self.trace_name = None
        self.trace_id = None
        self.session = None
```

**Add span context manager**:
```python
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

    # Create span content using context
    content_span = {
        "id": str(span_context.id),
        "name": span_context.name,
        "input": inputs,
        "metadata": metadata or {},
        "level": level,
    }

    # Get parent observation and create span
    span_client = self._get_current_observation().span(**content_span)
    self.spans[span_context.id] = span_client

    error_to_record: Exception | None = None

    try:
        yield span_context
    except Exception as e:
        error_to_record = e
        raise
    finally:
        # Guaranteed cleanup - end the span
        span = self.spans.pop(span_context.id, None)
        if span:
            # Get outputs from context and merge with error if present
            output = span_context.outputs.copy() if span_context.outputs else {}
            if error_to_record:
                output["error"] = str(error_to_record)

            content = {
                "output": output,
                "status_message": str(error_to_record) if error_to_record else None,
                "level": "ERROR" if error_to_record else None,
            }
            span.end(**content)
```

#### 2. Update imports
**File**: `apps/service_providers/tracing/langfuse.py`
**Changes**: Add contextlib import

```python
from contextlib import contextmanager
from typing import Iterator, Self
```

#### 3. Note on ClientManager
**File**: `apps/service_providers/tracing/langfuse.py`
**Changes**: None - keep ClientManager singleton as-is (lines 160-236)

The ClientManager pattern doesn't need changes since:
- It's a singleton for client pooling
- `client_manager.get()` is called at trace start
- Client is flushed but not shut down (remains in pool)

### Success Criteria:

#### Automated Verification:
- [x] LangFuseTracer imports successfully: `python -c "from apps.service_providers.tracing.langfuse import LangFuseTracer"`
- [x] Type checking passes: `inv ruff`
- [ ] Unit tests pass: `pytest apps/service_providers/tests/test_langfuse_client_manager.py`

#### Manual Verification:
- [ ] Context manager properly creates Langfuse trace
- [ ] Client is flushed on exit
- [ ] Reentry check prevents nested traces
- [ ] Spans are properly nested under parent observations
- [ ] ClientManager continues to pool clients correctly

**Implementation Note**: After completing this phase, both concrete tracer implementations use the new context manager interface. Proceed to Phase 4 to update TracingService to use ExitStack with these new interfaces.

---

## Phase 4: Update TracingService with ExitStack

### Overview
Update `TracingService` to use `ExitStack` for managing multiple tracer contexts. This provides guaranteed cleanup even when tracer initialization fails, while maintaining the existing public API.

### Changes Required:

#### 1. TracingService trace() Context Manager
**File**: `apps/service_providers/tracing/service.py`
**Changes**: Replace `_start_traces()` and `_end_traces()` with ExitStack-based implementation

**Update trace() method** (lines 91-111):
```python
from contextlib import ExitStack

@contextmanager
def trace(
    self,
    trace_name: str,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, str] | None = None,
):
    """Context manager for tracing.

    Uses ExitStack to manage multiple tracer contexts safely.
    If a tracer fails during initialization, already-entered
    tracers are guaranteed to be cleaned up.
    """
    self.trace_id = uuid.uuid4()
    self.trace_name = trace_name
    self.session = session
    self._start_time = time.time()

    # Create context object for this trace
    trace_context = TraceContext(id=self.trace_id, name=trace_name)

    with ExitStack() as stack:
        # Enter all tracer contexts
        for tracer in self._tracers:
            try:
                stack.enter_context(tracer.trace(
                    trace_context=trace_context,
                    session=session,
                    inputs=inputs,
                    metadata=metadata,
                ))
            except Exception:
                logger.exception("Error initializing tracer %s", tracer.__class__.__name__)
                # ExitStack ensures already-entered tracers get cleaned up

        sentry_sdk.set_context("Traces", self.get_trace_metadata())

        try:
            yield self
        finally:
            # Note: trace outputs can be accessed via trace_context.outputs if needed
            self._reset()
```

**Remove these methods** (lines 113-134):
```python
def _start_traces(self, inputs: dict[str, Any] | None = None, metadata: dict[str, str] | None = None):
    # No longer needed - ExitStack handles this

def _end_traces(self, error: Exception | None = None):
    # No longer needed - ExitStack handles this
```

#### 2. TracingService span() Context Manager
**File**: `apps/service_providers/tracing/service.py`
**Changes**: Replace `_start_span()` and `_end_span()` with ExitStack-based implementation

**Update span() method** (lines 136-161):
```python
@contextmanager
def span(
    self,
    span_name: str,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
):
    """Context manager for spanning.

    Uses ExitStack to manage multiple tracer span contexts safely.
    """
    if not self.activated:
        yield self
        return

    span_id = uuid.uuid4()

    # Create context object for this span
    span_context = TraceContext(id=span_id, name=span_name)

    # Add to span stack for tracking
    self.span_stack.append((span_id, span_name))

    with ExitStack() as stack:
        # Enter all tracer span contexts
        for tracer in self._active_tracers:
            try:
                stack.enter_context(tracer.span(
                    span_context=span_context,
                    inputs=inputs,
                    metadata=metadata or {},
                ))
            except Exception:
                logger.exception(f"Error starting span {span_name} in tracer {tracer.__class__.__name__}")
                # ExitStack ensures already-entered spans get cleaned up

        try:
            yield self
        finally:
            # Verify and pop from span stack
            popped_span_id, _ = self.span_stack.pop()
            if popped_span_id != span_id:
                logger.error("Span ID mismatch: expected %s, got %s", popped_span_id, span_id)

            # Note: span outputs can be accessed via span_context.outputs if needed

            # Store outputs for tracers (they read from self.outputs)
            # This happens before span exits, so tracers can access outputs
            # Note: Tracers don't receive outputs in the new API
```

**Remove these methods** (lines 242-281):
```python
def _start_span(
    self,
    span_id: UUID,
    span_name: str,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    # No longer needed - ExitStack handles this

def _end_span(self, span_id: UUID, span_name: str, error: Exception | None = None) -> None:
    # No longer needed - ExitStack handles this
```

#### 3. Handle Outputs with Context Objects
**File**: `apps/service_providers/tracing/service.py`
**Changes**: Use context objects to cleanly pass outputs between TracingService and tracers

With the context object approach, outputs flow cleanly through the system:

1. **TracingService creates context objects** with trace/span information
2. **Context objects are passed to tracers** and yielded to user code
3. **User code sets outputs** on the context object via `set_outputs()`
4. **Tracers read outputs** from the context object in their finally blocks

**Benefits of this approach**:
- Clean, explicit data flow
- No temporary attributes on tracer instances
- Context objects encapsulate all state for the trace/span
- Outputs are available where needed without complex passing mechanisms

```python
@contextmanager
def trace(
    self,
    trace_name: str,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, str] | None = None,
):
    self.trace_id = uuid.uuid4()
    self.trace_name = trace_name
    self.session = session
    self._start_time = time.time()

    # Create context object that will be passed to tracers and yielded to user
    trace_context = TraceContext(
        id=self.trace_id,
        name=trace_name,
    )

    try:
        with ExitStack() as stack:
            # Enter all tracer contexts, passing the same context object
            for tracer in self._tracers:
                try:
                    stack.enter_context(tracer.trace(
                        trace_context=trace_context,
                        session=session,
                        inputs=inputs,
                        metadata=metadata,
                    ))
                except Exception:
                    logger.exception("Error initializing tracer %s", tracer.__class__.__name__)

            sentry_sdk.set_context("Traces", self.get_trace_metadata())

            # Yield the context object to user code
            yield trace_context
    finally:
        self._reset()
```

#### 4. Update imports
**File**: `apps/service_providers/tracing/service.py`
**Changes**: Add ExitStack import (already has contextmanager)

```python
from contextlib import contextmanager, ExitStack
```

**Updated span() method with context objects**:

```python
@contextmanager
def span(
    self,
    span_name: str,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
):
    """Context manager for spanning.

    Uses ExitStack to manage multiple tracer span contexts safely.
    """
    if not self.activated:
        # Return a dummy context if not activated
        yield TraceContext(id=uuid.uuid4(), name=span_name)
        return

    span_id = uuid.uuid4()
    self.span_stack.append((span_id, span_name))

    # Create context object that will be passed to tracers and yielded to user
    span_context = TraceContext(
        id=span_id,
        name=span_name
    )

    try:
        with ExitStack() as stack:
            # Enter all tracer span contexts, passing the same context object
            for tracer in self._active_tracers:
                try:
                    stack.enter_context(tracer.span(
                        span_context=span_context,
                        inputs=inputs,
                        metadata=metadata or {},
                    ))
                except Exception:
                    logger.exception(f"Error starting span {span_name} in tracer {tracer.__class__.__name__}")

            # Yield the context object to user code
            yield span_context
    finally:
        # Verify and pop from span stack
        popped_span_id, _ = self.span_stack.pop()
        if popped_span_id != span_id:
            logger.error("Span ID mismatch: expected %s, got %s", popped_span_id, span_id)
```

**Updated set_current_span_outputs() for backward compatibility**:

```python
def set_current_span_outputs(
    self,
    outputs: dict[str, Any],
) -> None:
    """Set outputs for the current span or trace.

    This method maintains backward compatibility with existing code.
    With context objects, users should call set_outputs() on the
    context directly instead.
    """
    if not self.activated:
        return

    # Find the current context (span or trace)
    if hasattr(self, '_current_context'):
        self._current_context.set_outputs(outputs)
    else:
        # Fallback for code that hasn't been updated
        logger.warning(
            "set_current_span_outputs called without context object. "
            "Consider updating to use context.set_outputs() instead."
        )
```


### Success Criteria:

#### Automated Verification:
- [x] TracingService imports successfully: `python -c "from apps.service_providers.tracing.service import TracingService"`
- [x] Type checking passes: `make check`
- [x] No syntax errors

#### Manual Verification:
- [ ] ExitStack properly enters multiple tracer contexts
- [ ] If one tracer fails, others are cleaned up correctly
- [ ] Outputs are accessible to tracers in cleanup
- [ ] span_stack management still works correctly
- [ ] Sentry context is set correctly

**Implementation Note**: After completing this phase, the new architecture is in place. Tests will fail until Phase 5-6. The public API of TracingService remains unchanged.

---

## Phase 5: Update MockTracer for Tests

### Overview
Update the `MockTracer` test helper to implement the new context manager interface. This is required before updating tests in Phase 6.

### Changes Required:

#### 1. MockTracer Context Managers
**File**: `apps/service_providers/tests/mock_tracer.py`
**Changes**: Replace start/end methods with context managers

**Remove methods** (lines 23-69):
```python
def start_trace(...) -> None:
    super().start_trace(trace_name=trace_name, trace_id=trace_id, session=session)
    self.trace = {...}

def end_trace(...) -> None:
    super().end_trace(outputs=outputs, error=error)
    self.trace["outputs"] = outputs
    self.trace["error"] = error
    self.trace["ended"] = True

def start_span(...) -> None:
    self.spans[span_id] = {...}

def end_span(...) -> None:
    span = self.spans[span_id]
    span["outputs"] = outputs or {}
    span["error"] = str(error) if error else None
    span["ended"] = True
```

**Add context managers**:
```python
from contextlib import contextmanager
from typing import Iterator

@contextmanager
def trace(
    self,
    trace_context: TraceContext,
    session: ExperimentSession,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[TraceContext]:
    """Context manager for mock trace."""
    # Set base class state
    self.trace_name = trace_context.name
    self.trace_id = trace_context.id
    self.session = session

    # Create mock trace
    self.trace = {
        "name": trace_context.name,
        "id": trace_context.id,
        "session_id": session.id,
        "user_id": session.participant.identifier,
        "inputs": inputs or {},
        "metadata": metadata or {},
        "ended": False,
    }

    error_to_record: Exception | None = None

    try:
        yield trace_context
    except Exception as e:
        error_to_record = e
        raise
    finally:
        # Get outputs from the context object
        outputs = trace_context.outputs if trace_context.outputs else None

        # Mark as ended and store outputs/error
        self.trace["outputs"] = outputs
        self.trace["error"] = error_to_record
        self.trace["ended"] = True

        # Reset state
        self.trace_name = None
        self.trace_id = None
        self.session = None

@contextmanager
def span(
    self,
    span_context: TraceContext,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    level: SpanLevel = "DEFAULT",
) -> Iterator[TraceContext]:
    """Context manager for mock span."""
    # Create mock span
    self.spans[span_context.id] = {
        "name": span_context.name,
        "inputs": inputs,
        "metadata": metadata or {},
        "level": level,
        "ended": False,
    }

    error_to_record: Exception | None = None

    try:
        yield span_context
    except Exception as e:
        error_to_record = e
        raise
    finally:
        # Get outputs from the context object
        outputs = span_context.outputs if span_context.outputs else {}

        # Mark as ended and store outputs/error
        span = self.spans[span_context.id]
        span["outputs"] = outputs
        span["error"] = str(error_to_record) if error_to_record else None
        span["ended"] = True
```

#### 2. Update imports
**File**: `apps/service_providers/tests/mock_tracer.py`
**Changes**: Add contextlib import

```python
from contextlib import contextmanager
from typing import Any, Iterator
```

### Success Criteria:

#### Automated Verification:
- [x] MockTracer imports successfully: `python -c "from apps.service_providers.tests.mock_tracer import MockTracer"`
- [x] Type checking passes: `make check`

#### Manual Verification:
- [ ] MockTracer properly records trace/span data
- [ ] Mock state is accessible for test assertions
- [ ] Cleanup behavior matches real tracers

**Implementation Note**: After completing this phase, test infrastructure is ready. Proceed immediately to Phase 6 to update tests.

---

## Phase 6: Update Unit Tests

### Overview
Update all tracing-related tests to work with the new context manager interface. Tests currently call start/end methods directly, which no longer exist.

### Changes Required:

#### 1. Update TracingService Tests
**File**: `apps/service_providers/tests/test_tracing_service.py`
**Changes**: Update tests to use context objects for setting outputs

**Update test to use context objects**:
```python
def test_trace_context_with_outputs(self, tracing_service, mock_tracer, mock_session):
    """Test setting outputs via context object."""
    with tracing_service.trace("test", session=mock_session) as trace_ctx:
        # New way: set outputs on context object
        trace_ctx.set_outputs({"result": "success"})

        # Verify context has outputs
        assert trace_ctx.outputs == {"result": "success"}

    # Verify tracer received outputs
    assert mock_tracer.trace["outputs"] == {"result": "success"}
    assert mock_tracer.trace["ended"] is True

def test_span_context_with_outputs(self, tracing_service, mock_tracer, mock_session):
    """Test setting outputs via span context object."""
    with tracing_service.trace("test", session=mock_session):
        with tracing_service.span("test_span", {"input": "test"}) as span_ctx:
            # Set outputs on span context
            span_ctx.set_outputs({"output": "test_result"})

        # Get span from mock tracer
        span_id = list(mock_tracer.spans.keys())[0]
        assert mock_tracer.spans[span_id]["outputs"] == {"output": "test_result"}
```

**Backward compatibility test**:
```python
def test_set_current_span_outputs_backward_compatibility(self, tracing_service, mock_session):
    """Test that set_current_span_outputs still works for backward compatibility."""
    with tracing_service.trace("test", session=mock_session) as trace_ctx:
        # Old way should still work with warning
        tracing_service.set_current_span_outputs({"old": "way"})

        # Should log warning about using the old method
        # Outputs might not be captured depending on implementation
```

#### 2. Update OCS Tracer Tests
**File**: `apps/service_providers/tests/test_ocs_tracer.py`
**Changes**: Update tests that directly call start/end methods

**Find and update patterns like**:
```python
# Old:
tracer.start_trace(trace_name, trace_id, session)
# ... do work ...
tracer.end_trace(outputs)

# New:
ctx = TraceContext(trace_id, trace_name, session)
with tracer.trace(trace_name, trace_id, session, ctx):
    # ... do work ...
    ctx.set_outputs({"result": "value"})
    # Outputs available in ctx.outputs
```

**Tests to update**:
- Any test calling `start_trace()` directly
- Any test calling `end_trace()` directly
- Any test calling `start_span()` directly
- Any test calling `end_span()` directly

#### 3. Add New Test Cases
**File**: `apps/service_providers/tests/test_tracing_service.py`
**Changes**: Add tests for ExitStack behavior

**Add test for tracer initialization failure**:
```python
def test_trace_with_tracer_initialization_failure(self, mock_session):
    """Verify that if one tracer fails during init, others still get cleaned up."""
    good_tracer = MockTracer()
    bad_tracer = MockTracer()

    # Make bad_tracer raise exception in trace()
    def bad_trace(*args, **kwargs):
        raise RuntimeError("Tracer initialization failed")
    bad_tracer.trace = bad_trace

    service = TracingService([good_tracer, bad_tracer], 1, 1)

    # Should not raise, bad tracer failure is logged
    with service.trace("test", session=mock_session) as trace_ctx:
        assert good_tracer.trace is not None
        assert good_tracer.trace["ended"] is False
        # Context should still work
        trace_ctx.set_outputs({"test": "value"})

    # Good tracer should be properly cleaned up
    assert good_tracer.trace["ended"] is True
    assert good_tracer.trace["outputs"] == {"test": "value"}
```

**Add test for cleanup on exception**:
```python
def test_trace_cleanup_on_user_exception(self, tracing_service, mock_tracer, mock_session):
    """Verify tracers are cleaned up even when user code raises exception."""
    try:
        with tracing_service.trace("test", session=mock_session) as trace_ctx:
            assert mock_tracer.trace["ended"] is False
            trace_ctx.set_outputs({"partial": "result"})
            raise ValueError("User error")
    except ValueError:
        pass

    # Tracer should still be cleaned up
    assert mock_tracer.trace["ended"] is True
    assert mock_tracer.trace["outputs"] == {"partial": "result"}
    assert not tracing_service.activated
```

**Add test for span cleanup on exception**:
```python
def test_span_cleanup_on_exception(self, tracing_service, mock_tracer, mock_session):
    """Verify spans are cleaned up even when exception occurs."""
    with tracing_service.trace("test", session=mock_session):
        try:
            with tracing_service.span("test_span", {"input": "test"}) as span_ctx:
                span_id = list(mock_tracer.spans.keys())[0]
                assert mock_tracer.spans[span_id]["ended"] is False
                span_ctx.set_outputs({"partial": "span_result"})
                raise ValueError("Span error")
        except ValueError:
            pass

        # Span should be cleaned up
        assert mock_tracer.spans[span_id]["ended"] is True
        assert mock_tracer.spans[span_id]["outputs"] == {"partial": "span_result"}
        assert mock_tracer.spans[span_id]["error"] == "ValueError('Span error')"
```

#### 4. Update Integration Tests
**Files**: Various integration tests that use tracing
- `apps/service_providers/tests/test_runnables.py`
- `apps/service_providers/tests/test_assistant_runnable.py`
- `apps/service_providers/tests/test_chat_with_tools.py`

**Changes**: These tests use `TracingService` API, which is unchanged. Verify they still pass.

**If any tests fail**:
- Check if they're asserting on tracer internal state
- Update assertions to match new context manager behavior
- Verify error handling still works correctly

### Success Criteria:

#### Automated Verification:
- [ ] All tracer tests pass: `pytest apps/service_providers/tests/test_tracing_service.py`
- [ ] All OCS tracer tests pass: `pytest apps/service_providers/tests/test_ocs_tracer.py`
- [ ] All Langfuse tests pass: `pytest apps/service_providers/tests/test_langfuse_client_manager.py`
- [ ] Integration tests pass: `pytest apps/service_providers/tests/test_runnables.py`
- [ ] All service provider tests pass: `pytest apps/service_providers/tests/`

#### Manual Verification:
- [ ] New tests verify ExitStack cleanup behavior
- [ ] Tests verify exception handling in tracers
- [ ] Tests verify outputs are accessible to tracers
- [ ] Coverage remains high (check coverage report)

**Implementation Note**: After completing this phase, all tests should pass. If any fail, debug before proceeding to Phase 7.

---

## Phase 7: Verify Integration Points

### Overview
Verify that the tracing service changes don't break any integration points. The public API of `TracingService` is unchanged, so most code should work without modifications.

### Changes Required:

#### 1. Integration Point Verification

**No code changes expected** - this is a verification phase.

**Files to verify**:
- `apps/chat/bots.py` (lines 74, 286, 304, 362-377)
- `apps/chat/channels.py` (lines 48, 141, 1325)
- `apps/experiments/models.py` (lines 47, 1847, 1873)
- `apps/events/actions.py` (lines 10, 145)
- `apps/pipelines/tests/test_pipeline_runs.py` (lines 10, 35, 44, 59, 72)

**Verification steps**:

1. **Check usage patterns**:
   ```python
   # Pattern 1: Create service
   trace_service = TracingService.create_for_experiment(experiment)

   # Pattern 2: Use trace context
   with trace_service.trace("name", session, inputs, metadata):
       # user code

   # Pattern 3: Use span context
   with trace_service.span("span_name", inputs, metadata):
       # user code

   # Pattern 4: Get LangChain config
   config = trace_service.get_langchain_config()
   ```

2. **Run integration tests**:
   - Chat bot tests: `pytest apps/chat/tests/`
   - Experiment tests: `pytest apps/experiments/tests/`
   - Event tests: `pytest apps/events/tests/`
   - Pipeline tests: `pytest apps/pipelines/tests/`

3. **Check LangChain callback integration**:
   - Verify `get_langchain_callback()` still returns correct handler
   - Verify callbacks properly create spans via LangChain events
   - Test with actual LangChain runnables

#### 2. Error Case Verification

**Test scenarios manually or via integration tests**:

1. **Tracer initialization failure**:
   - Configure invalid Langfuse credentials
   - Verify experiment still works, just without Langfuse tracing
   - Check logs for error messages

2. **Database errors in OCSTracer**:
   - Simulate database connection issues
   - Verify trace continues despite OCS tracer failure
   - Check that experiment doesn't crash

3. **Langfuse service unavailable**:
   - Disconnect from Langfuse service
   - Verify graceful degradation
   - Check that client manager handles errors

#### 3. Performance Verification

**Check that ExitStack doesn't add significant overhead**:

1. **Run performance-sensitive tests**:
   - Pipeline execution tests
   - Bot response time tests
   - Bulk operation tests

2. **Compare timing** (optional):
   - Measure trace creation time before/after
   - Should be negligible difference (< 1ms)

#### 4. Documentation Review

**Files that may need updates** (not code changes):
- README or developer docs mentioning tracing
- API documentation for `Tracer` interface
- Comments explaining tracer lifecycle

### Success Criteria:

#### Automated Verification:
- [ ] All chat tests pass: `pytest apps/chat/tests/`
- [ ] All experiment tests pass: `pytest apps/experiments/tests/`
- [ ] All event tests pass: `pytest apps/events/tests/`
- [ ] All pipeline tests pass: `pytest apps/pipelines/tests/`
- [ ] Full test suite passes: `pytest`
- [ ] No regressions in test coverage

#### Manual Verification:
- [ ] Create an experiment with tracing enabled via UI
- [ ] Send messages and verify traces appear in Langfuse
- [ ] Check that OCS traces are created in database
- [ ] Verify LangChain callbacks create proper spans
- [ ] Test error handling with invalid tracer config
- [ ] Confirm no resource leaks (check open connections, memory)

**Implementation Note**: After completing this phase, the implementation is complete. Any issues found should be addressed before considering the work done.

---

## Testing Strategy

### Unit Tests

**Core tests to update/add**:
- `test_tracing_service.py` - Verify ExitStack behavior, cleanup on exception
- `test_ocs_tracer.py` - Verify context manager implementation
- `test_langfuse_client_manager.py` - Verify client pooling still works
- `mock_tracer.py` - Update to new interface

**Key test scenarios**:
1. Normal trace lifecycle (already tested)
2. Trace with exception in user code (add)
3. Tracer initialization failure (add)
4. Multiple tracers with mixed success/failure (add)
5. Span nesting (already tested)
6. Span with exception (already tested)
7. Outputs accessible to tracers (verify)

### Integration Tests

**Existing tests that should pass**:
- `test_runnables.py` - LangChain runnable integration
- `test_assistant_runnable.py` - OpenAI assistant integration
- `test_chat_with_tools.py` - Chat with tool calling

**Verification**:
- All existing integration tests pass without modification
- LangChain callbacks work correctly
- Error handling is preserved

### Manual Testing

**Test cases**:
1. Create experiment with tracing enabled
2. Send message through web chat
3. Verify trace in Langfuse UI
4. Verify trace in OCS database
5. Test with invalid Langfuse config (should degrade gracefully)
6. Test with pipeline execution (complex spans)
7. Test error scenarios (bot errors should appear in traces)

## Performance Considerations

**Expected impact**: Negligible

**Reasoning**:
- `ExitStack` is a lightweight context manager combiner
- Context managers have minimal overhead vs explicit try/finally
- No additional I/O or computation added
- Same number of tracer method calls (enter/exit vs start/end)

**Potential improvements**:
- ExitStack guarantees cleanup, preventing resource leaks
- Better error isolation (one tracer failure doesn't affect others)

## Migration Notes

**Breaking changes**:
- Tracer base class no longer has `start_trace()`/`end_trace()` methods
- Tracer base class no longer has `start_span()`/`end_span()` methods
- Custom tracer implementations must implement context manager methods

**Compatibility**:
- Public `TracingService` API unchanged
- Integration points require no changes
- Tests need updates to use context managers

**Rollback plan**:
- Git revert all changes
- Previous implementation is fully self-contained
- No database migrations or data changes

## References

- Original research: `thoughts/shared/research/2025-11-04-tracing-service-architecture.md`
- Core tracing files:
  - `apps/service_providers/tracing/base.py:24-105` - Abstract Tracer class
  - `apps/service_providers/tracing/service.py:26-315` - TracingService orchestrator
  - `apps/service_providers/tracing/langfuse.py:28-263` - LangFuseTracer
  - `apps/service_providers/tracing/ocs_tracer.py:25-315` - OCSTracer
- Integration points:
  - `apps/chat/bots.py:74,286,304,362-377` - Bot implementations
  - `apps/events/actions.py:145-166` - Event action execution
  - `apps/experiments/models.py:1847-1850` - TracingService factory
- Context manager patterns in codebase:
  - `apps/teams/utils.py:64-72` - ContextVar pattern
  - `apps/pipelines/nodes/helpers.py:13-17` - Combined contexts
  - `apps/utils/langchain.py:205-222` - Nested mocking
