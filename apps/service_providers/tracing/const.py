from typing import Literal

# trace provider type of internal tracer (currently not built)
OCS_TRACE_PROVIDER = "ocs"

# Internal tracer that only bills LLM calls; keeps no trace of its own (see UsageOnlyTracer).
USAGE_ONLY_TRACE_PROVIDER = "ocs_usage"

# Internal tracer that keeps a run's LLM and tool calls in memory; writes nothing (see CaptureTracer).
CAPTURE_TRACE_PROVIDER = "ocs_capture"

# LangChain custom event a custom action tool dispatches for each HTTP request it makes.
CUSTOM_ACTION_REQUEST_EVENT = "custom_action_request"

SpanLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]
