from typing import Literal

# trace provider type of internal tracer (currently not built)
OCS_TRACE_PROVIDER = "ocs"

# Internal tracer that only bills LLM calls; keeps no trace of its own (see UsageOnlyTracer).
USAGE_ONLY_TRACE_PROVIDER = "ocs_usage"

SpanLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]
