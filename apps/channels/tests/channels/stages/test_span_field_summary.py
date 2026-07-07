"""Tests for the declarative span input/output field serialization.

Covers _summarize_span_fields / _to_trace_value: dotted-path resolution,
trace-safe rendering, and the guarantee that serialization never raises.
"""

from datetime import date, datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from apps.channels.channels_v2.stages.base import (
    _SPAN_LIST_LIMIT,
    ProcessingStage,
    _summarize_span_fields,
    _to_trace_value,
)
from apps.files.models import File

from ..conftest import make_context, make_trace_service


class _Color(Enum):
    RED = "red"


class _Exploding:
    @property
    def boom(self):
        raise RuntimeError("do not read me")


class TestToTraceValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(None, None, id="none"),
            pytest.param("hi", "hi", id="str"),
            pytest.param(7, 7, id="int"),
            pytest.param(True, True, id="bool"),
            pytest.param(1.5, 1.5, id="float"),
            pytest.param(_Color.RED, "red", id="enum_to_value"),
        ],
    )
    def test_primitives_pass_through(self, value, expected):
        assert _to_trace_value(value) == expected

    def test_datetime_renders_isoformat(self):
        assert _to_trace_value(date(2026, 7, 7)) == "2026-07-07"
        assert _to_trace_value(datetime(2026, 7, 7, 8, 30)) == "2026-07-07T08:30:00"

    def test_unknown_object_collapses_to_type_name(self):
        assert _to_trace_value(object()) == "<object>"

    def test_model_renders_id_and_verbose_name(self):
        rendered = _to_trace_value(File())  # unsaved -- no DB needed
        assert rendered == {"id": None, "model": str(File._meta.verbose_name)}

    def test_lists_are_bounded(self):
        rendered = _to_trace_value(list(range(_SPAN_LIST_LIMIT + 10)))
        assert rendered == list(range(_SPAN_LIST_LIMIT))

    def test_nested_collections_are_depth_capped(self):
        # A list nested two levels down is summarized rather than expanded.
        rendered = _to_trace_value([[["deep"]]])
        assert rendered == [["[1 items]"]]


class TestSummarizeSpanFields:
    def test_resolves_dotted_paths(self):
        ctx = SimpleNamespace(message=SimpleNamespace(content_type="text"), user_query="hello")
        assert _summarize_span_fields(ctx, ("message.content_type", "user_query")) == {
            "message.content_type": "text",
            "user_query": "hello",
        }

    def test_missing_attribute_is_skipped(self):
        ctx = SimpleNamespace(user_query="hi")
        assert _summarize_span_fields(ctx, ("does_not_exist", "user_query")) == {"user_query": "hi"}

    def test_none_along_path_yields_none(self):
        ctx = SimpleNamespace(participant=None)
        assert _summarize_span_fields(ctx, ("participant.id",)) == {"participant.id": None}

    def test_serialization_failure_degrades_to_placeholder(self):
        assert _summarize_span_fields(_Exploding(), ("boom",)) == {"boom": "<unserializable>"}

    def test_legacy_named_segments_are_aliased_in_display_keys(self):
        ctx = SimpleNamespace(
            experiment_session=SimpleNamespace(status="active", experiment_versions=[1, 2]),
            experiment_channel=SimpleNamespace(platform="connect"),
        )
        summary = _summarize_span_fields(
            ctx,
            ("experiment_session.status", "experiment_session.experiment_versions", "experiment_channel.platform"),
        )
        assert summary == {
            "session.status": "active",
            "session.chatbot_versions": [1, 2],
            "channel.platform": "connect",
        }


class _StageWithFields(ProcessingStage):
    span_input_fields = ("user_query",)
    span_output_fields = ("formatted_message",)

    def process(self, ctx):
        ctx.formatted_message = "formatted!"


class TestFieldsFlowToSpan:
    def test_declared_fields_land_on_the_span(self):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service, user_query="what is up")

        _StageWithFields()(ctx)

        # inputs captured at span open, from span_input_fields
        assert trace_service.span.call_args.kwargs["inputs"] == {"user_query": "what is up"}
        # outputs captured after process, from span_output_fields
        assert span.set_outputs.call_args[0][0] == {"formatted_message": "formatted!"}
