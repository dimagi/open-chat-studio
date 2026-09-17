"""Checks that the trace migration command still speaks the installed SDK's ingestion types.

`_transform_trace_to_ingestion_batch` builds Fern-generated request bodies by hand, so a
field rename or a tightened validator in a Langfuse release breaks it silently until a
migration is run. These tests exercise the transform with real SDK models and serialize
the result the way `api.ingestion.batch` does.
"""

import datetime as dt

import pytest
from langfuse.api import (
    IngestionEvent_GenerationCreate,
    IngestionEvent_ScoreCreate,
    IngestionEvent_SpanCreate,
    IngestionEvent_TraceCreate,
    ObservationLevel,
    ObservationsView,
    ScoreV1_Numeric,
    TraceWithFullDetails,
    Usage,
)

from apps.service_providers.management.commands.migrate_langfuse_data import (
    _transform_trace_to_ingestion_batch,
)

START = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def _observation(**overrides):
    defaults = {
        "id": "obs-1",
        "trace_id": "trace-1",
        "type": "SPAN",
        "name": "obs",
        "start_time": START,
        "end_time": START + dt.timedelta(seconds=1),
        "metadata": {"k": "v"},
        "input": {"in": 1},
        "output": {"out": 2},
        "level": ObservationLevel.DEFAULT,
        "status_message": None,
        "parent_observation_id": None,
        "version": None,
        "environment": "default",
        "model": None,
        "model_parameters": None,
        "usage": Usage(input=0, output=0, total=0),
        "usage_details": {},
        "cost_details": {},
        "completion_start_time": None,
        "prompt_name": None,
        "prompt_version": None,
    }
    return ObservationsView(**{**defaults, **overrides})


def _trace(observations, scores=()):
    return TraceWithFullDetails(
        id="trace-1",
        timestamp=START,
        name="root",
        user_id="user-1",
        session_id="session-1",
        input={"question": "hi"},
        output={"answer": "hello"},
        metadata={"team": "acme"},
        tags=["safety_layer:blocked"],
        public=False,
        release=None,
        version=None,
        environment="default",
        html_path="/traces/trace-1",
        latency=1.0,
        total_cost=0.0,
        observations=list(observations),
        scores=list(scores),
    )


def test_transform_preserves_the_trace_and_maps_each_observation_type():
    span = _observation(id="span-1", type="SPAN", name="pipeline")
    generation = _observation(
        id="gen-1",
        type="GENERATION",
        name="llm",
        parent_observation_id="span-1",
        model="gpt-4o",
        model_parameters={"temperature": 0.1},
        usage=Usage(input=10, output=5, total=15, unit="TOKENS"),
    )
    event = _observation(id="event-1", type="EVENT", name="custom", parent_observation_id="span-1")

    batch = _transform_trace_to_ingestion_batch(_trace([span, generation, event]))

    trace_event, span_event, generation_event, event_event = batch
    assert isinstance(trace_event, IngestionEvent_TraceCreate)
    assert isinstance(span_event, IngestionEvent_SpanCreate)
    assert isinstance(generation_event, IngestionEvent_GenerationCreate)
    assert trace_event.body.id == "trace-1"
    assert trace_event.body.session_id == "session-1"
    assert trace_event.body.tags == ["safety_layer:blocked"]
    assert generation_event.body.model == "gpt-4o"
    assert generation_event.body.usage.total == 15

    # Parent links are remapped to the new observation ids, not the source ones.
    assert span_event.body.parent_observation_id is None
    assert generation_event.body.parent_observation_id == span_event.body.id
    assert event_event.body.parent_observation_id == span_event.body.id
    assert generation_event.body.id != "gen-1"


def test_transform_attaches_scores_to_the_remapped_observation():
    generation = _observation(id="gen-1", type="GENERATION", name="llm")
    score = ScoreV1_Numeric(
        id="score-1",
        trace_id="trace-1",
        observation_id="gen-1",
        name="helpfulness",
        value=0.8,
        source="API",
        data_type="NUMERIC",
        timestamp=START,
        environment="default",
        created_at=START,
        updated_at=START,
        metadata={},
    )

    batch = _transform_trace_to_ingestion_batch(_trace([generation], scores=[score]))

    score_event = batch[-1]
    assert isinstance(score_event, IngestionEvent_ScoreCreate)
    assert score_event.body.value == 0.8
    assert score_event.body.observation_id == batch[1].body.id


@pytest.mark.parametrize(
    "observation_type",
    ["SPAN", "GENERATION", "EVENT"],
    ids=["span", "generation", "event"],
)
def test_ingestion_events_serialize_for_the_batch_endpoint(observation_type):
    """`api.ingestion.batch` JSON-serializes these bodies; a model change that the transform
    survives can still fail here.
    """
    batch = _transform_trace_to_ingestion_batch(_trace([_observation(type=observation_type)]))

    for event in batch:
        payload = event.dict(by_alias=True, exclude_none=True)
        assert payload["type"]
        assert payload["body"]
