from datetime import timedelta

PREVIEW_SAMPLE_SIZE = 10

# How long the results page waits for a completed run's finalization before settling on the
# aggregates it can see. Finalization is its own task (see ADR-0047), so a lost dispatch or a
# dead worker would otherwise leave the page polling for aggregates that are never coming.
FINALIZATION_GRACE = timedelta(minutes=10)

EVALUATION_RUN_FIXED_HEADERS = [
    "id",
    "session",
    "source_session",
    "source_experiment_id",
    "Dataset Input",
    "Dataset Output",
    "Generated Response",
]
