import json
from datetime import datetime, timedelta

import pytest
from django.http import QueryDict

from apps.annotations.models import Tag
from apps.trace.filters import TraceFilter
from apps.trace.models import Trace, TraceStatus
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.traces import SpanFactory, TraceFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


@pytest.mark.django_db()
class TestTraceFilter:
    @pytest.fixture()
    def experiment(self, team):
        return ExperimentFactory(team=team)

    @pytest.fixture()
    def participant(self, team):
        return ParticipantFactory(team=team, identifier="test_participant")

    @pytest.fixture()
    def trace(self, team, experiment, participant):
        return TraceFactory(
            team=team, experiment=experiment, participant=participant, status=TraceStatus.SUCCESS, duration=1000
        )

    @pytest.fixture()
    def span(self, trace, team):
        return SpanFactory(trace=trace, team=team, name="test_span")

    def _create_filter_and_apply(self, queryset, column, operator, value, timezone="UTC"):
        """Helper method to create a filter and apply it"""
        params = {
            "filter_0_column": column,
            "filter_0_operator": operator,
            "filter_0_value": value,
        }
        query_params = QueryDict("", mutable=True)
        query_params.update(params)
        filter_instance = TraceFilter()
        return filter_instance.apply(queryset, FilterParams(query_params), timezone)

    def test_participant_filter_equals(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        result = self._create_filter_and_apply(queryset, "participant", Operators.EQUALS, "test_participant")
        assert trace in result

        result = self._create_filter_and_apply(queryset, "participant", Operators.EQUALS, "other_participant")
        assert trace not in result

    def test_participant_filter_contains(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        result = self._create_filter_and_apply(queryset, "participant", Operators.CONTAINS, "test")
        assert trace in result

        result = self._create_filter_and_apply(queryset, "participant", Operators.CONTAINS, "xyz")
        assert trace not in result

    def test_participant_filter_does_not_contain(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        result = self._create_filter_and_apply(queryset, "participant", Operators.DOES_NOT_CONTAIN, "xyz")
        assert trace in result

        result = self._create_filter_and_apply(queryset, "participant", Operators.DOES_NOT_CONTAIN, "test")
        assert trace not in result

    def test_participant_filter_starts_with(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        result = self._create_filter_and_apply(queryset, "participant", Operators.STARTS_WITH, "test")
        assert trace in result

        result = self._create_filter_and_apply(queryset, "participant", Operators.STARTS_WITH, "xyz")
        assert trace not in result

    def test_participant_filter_ends_with(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        result = self._create_filter_and_apply(queryset, "participant", Operators.ENDS_WITH, "participant")
        assert trace in result

        result = self._create_filter_and_apply(queryset, "participant", Operators.ENDS_WITH, "xyz")
        assert trace not in result

    def test_participant_filter_any_of(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        value = json.dumps(["test_participant", "other_participant"])
        result = self._create_filter_and_apply(queryset, "participant", Operators.ANY_OF, value)
        assert trace in result

        value = json.dumps(["other1", "other2"])
        result = self._create_filter_and_apply(queryset, "participant", Operators.ANY_OF, value)
        assert trace not in result

    def test_tags_filter_any_of(self, trace, span, team):
        tag1 = Tag.objects.create(name="tag1", team=team)
        tag2 = Tag.objects.create(name="tag2", team=team)
        span.add_tag(tag1, team)
        span.add_tag(tag2, team)

        queryset = Trace.objects.filter(team=team)

        value = json.dumps(["tag1", "tag3"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching tags
        value = json.dumps(["tag3", "tag4"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.ANY_OF, value)
        assert trace not in result

    def test_tags_filter_all_of(self, trace, span, team):
        tag1 = Tag.objects.create(name="tag1", team=team)
        tag2 = Tag.objects.create(name="tag2", team=team)
        tag3 = Tag.objects.create(name="tag3", team=team)
        span.add_tag(tag1, team)
        span.add_tag(tag2, team)
        span.add_tag(tag3, team)

        queryset = Trace.objects.filter(team=team)

        # Test ALL_OF operator - both tags present
        value = json.dumps(["tag1", "tag2"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.ALL_OF, value)
        assert trace in result

        # Test ALL_OF operator - one tag missing
        value = json.dumps(["tag1", "tag4"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.ALL_OF, value)
        assert trace not in result

    def test_tags_filter_excludes(self, trace, span, team):
        tag1 = Tag.objects.create(name="tag1", team=team)
        Tag.objects.create(name="tag2", team=team)
        span.add_tag(tag1, team)

        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - tag not present (should include)
        value = json.dumps(["tag2", "tag3"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - tag present (should exclude)
        value = json.dumps(["tag1", "tag3"])
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.EXCLUDES, value)
        assert trace not in result

    def test_tags_filter_invalid_json(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test invalid JSON
        result = self._create_filter_and_apply(queryset, "span_tags", Operators.ANY_OF, "invalid_json")
        # Should return original queryset since filter returns None
        assert trace in result

    def test_remote_id_filter_any_of(self, trace, team):
        trace.participant.remote_id = "remote123"
        trace.participant.save()

        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator
        value = json.dumps(["remote123", "remote456"])
        result = self._create_filter_and_apply(queryset, "remote_id", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching value
        value = json.dumps(["remote456", "remote789"])
        result = self._create_filter_and_apply(queryset, "remote_id", Operators.ANY_OF, value)
        assert trace not in result

    def test_remote_id_filter_excludes(self, trace, team):
        trace.participant.remote_id = "remote123"
        trace.participant.save()

        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - remote_id not in list (should include)
        value = json.dumps(["remote456", "remote789"])
        result = self._create_filter_and_apply(queryset, "remote_id", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - remote_id in list (should exclude)
        value = json.dumps(["remote123", "remote456"])
        result = self._create_filter_and_apply(queryset, "remote_id", Operators.EXCLUDES, value)
        assert trace not in result

    def test_timestamp_filter_range(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test RANGE operator with hours
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.RANGE, "24h")
        assert trace in result

        # Test RANGE operator with days
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.RANGE, "7d")
        assert trace in result

        # Test RANGE operator with minutes
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.RANGE, "60m")
        assert trace in result

    def test_timestamp_filter_on(self, trace, team):
        queryset = Trace.objects.filter(team=team)
        today = datetime.now().date()

        # Test ON operator
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.ON, today.isoformat())
        assert trace in result

    def test_timestamp_filter_before(self, trace, team):
        queryset = Trace.objects.filter(team=team)
        tomorrow = datetime.now().date() + timedelta(days=1)

        # Test BEFORE operator
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.BEFORE, tomorrow.isoformat())
        assert trace in result

    def test_timestamp_filter_after(self, trace, team):
        queryset = Trace.objects.filter(team=team)
        yesterday = datetime.now().date() - timedelta(days=1)

        # Test AFTER operator
        result = self._create_filter_and_apply(queryset, "timestamp", Operators.AFTER, yesterday.isoformat())
        assert trace in result

    def test_span_name_filter_any_of(self, trace, span, team):
        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator
        value = json.dumps(["test_span", "other_span"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching value
        value = json.dumps(["other_span", "another_span"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.ANY_OF, value)
        assert trace not in result

    def test_span_name_filter_all_of(self, trace, span, team):
        # Create additional spans
        SpanFactory(trace=trace, team=team, name="span2")
        SpanFactory(trace=trace, team=team, name="span3")

        queryset = Trace.objects.filter(team=team)

        # Test ALL_OF operator - both spans present
        value = json.dumps(["test_span", "span2"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.ALL_OF, value)
        assert trace in result

        # Test ALL_OF operator - one span missing
        value = json.dumps(["test_span", "missing_span"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.ALL_OF, value)
        assert trace not in result

    def test_span_name_filter_excludes(self, trace, span, team):
        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - span not present (should include)
        value = json.dumps(["other_span", "another_span"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - span present (should exclude)
        value = json.dumps(["test_span", "other_span"])
        result = self._create_filter_and_apply(queryset, "span_name", Operators.EXCLUDES, value)
        assert trace not in result

    # Test experiment filter
    def test_experiment_filter_any_of(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator
        value = json.dumps([str(trace.experiment.id), "99999999"])
        result = self._create_filter_and_apply(queryset, "experiment", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching value
        value = json.dumps(["99999999", "88888888"])
        result = self._create_filter_and_apply(queryset, "experiment", Operators.ANY_OF, value)
        assert trace not in result

    def test_experiment_filter_excludes(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - experiment not in list (should include)
        value = json.dumps(["99999999", "88888888"])
        result = self._create_filter_and_apply(queryset, "experiment", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - experiment in list (should exclude)
        value = json.dumps([str(trace.experiment.id), "99999999"])
        result = self._create_filter_and_apply(queryset, "experiment", Operators.EXCLUDES, value)
        assert trace not in result

    # Test status filter
    def test_status_filter_any_of(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator
        value = json.dumps([TraceStatus.SUCCESS, TraceStatus.ERROR])
        result = self._create_filter_and_apply(queryset, "status", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching value
        value = json.dumps([TraceStatus.ERROR, TraceStatus.PENDING])
        result = self._create_filter_and_apply(queryset, "status", Operators.ANY_OF, value)
        assert trace not in result

    def test_status_filter_excludes(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - status not in list (should include)
        value = json.dumps([TraceStatus.ERROR, TraceStatus.PENDING])
        result = self._create_filter_and_apply(queryset, "status", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - status in list (should exclude)
        value = json.dumps([TraceStatus.SUCCESS, TraceStatus.ERROR])
        result = self._create_filter_and_apply(queryset, "status", Operators.EXCLUDES, value)
        assert trace not in result

    # Test edge cases and error handling
    def test_invalid_column(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test invalid column name
        result = self._create_filter_and_apply(queryset, "invalid_column", Operators.EQUALS, "value")
        # Should return original queryset
        assert trace in result

    def test_empty_value(self, trace, team):
        queryset = Trace.objects.filter(team=team)

        # Test empty value
        result = self._create_filter_and_apply(queryset, "participant", Operators.EQUALS, "")
        # Should return original queryset since empty values are ignored
        assert trace in result

    def test_multiple_filters(self, trace, span, team):
        # Create tags for span
        tag1 = Tag.objects.create(name="tag1", team=team)
        span.add_tag(tag1, team)

        # Apply multiple filters
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "test_participant",
            "filter_1_column": "span_tags",
            "filter_1_operator": Operators.ANY_OF,
            "filter_1_value": json.dumps(["tag1"]),
        }
        query_params = QueryDict("", mutable=True)
        query_params.update(params)
        queryset = Trace.objects.filter(team=team)
        filter_instance = TraceFilter()
        result = filter_instance.apply(queryset, FilterParams(query_params), "UTC")

        assert trace in result

        # Test with one filter not matching
        params["filter_1_value"] = json.dumps(["tag2"])
        query_params = QueryDict("", mutable=True)
        query_params.update(params)
        filter_instance = TraceFilter()
        result = filter_instance.apply(queryset, FilterParams(query_params), "UTC")

        assert trace not in result

    # Test message tags filter
    def test_message_tags_filter_any_of_input_message(self, trace, team):
        """Test message tags filter with tags on input message."""
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.utils.factories.experiment import ChatFactory

        # Create a chat and input message with tags
        chat = ChatFactory(team=team)
        input_message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test input")
        tag1 = Tag.objects.create(name="important", team=team, is_system_tag=False)
        Tag.objects.create(name="urgent", team=team, is_system_tag=False)
        input_message.add_tag(tag1, team)

        # Link the message to the trace
        trace.input_message = input_message
        trace.save()

        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator - tag present on input message
        value = json.dumps(["important", "other"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.ANY_OF, value)
        assert trace in result

        # Test non-matching tags
        value = json.dumps(["urgent", "other"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.ANY_OF, value)
        assert trace not in result

    def test_message_tags_filter_any_of_output_message(self, trace, team):
        """Test message tags filter with tags on output message."""
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.utils.factories.experiment import ChatFactory

        # Create a chat and output message with tags
        chat = ChatFactory(team=team)
        output_message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.AI, content="Test output")
        tag1 = Tag.objects.create(name="important", team=team, is_system_tag=False)
        output_message.add_tag(tag1, team)

        # Link the message to the trace
        trace.output_message = output_message
        trace.save()

        queryset = Trace.objects.filter(team=team)

        # Test ANY_OF operator - tag present on output message
        value = json.dumps(["important"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.ANY_OF, value)
        assert trace in result

    def test_message_tags_filter_all_of(self, trace, team):
        """Test message tags filter with ALL_OF operator."""
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.utils.factories.experiment import ChatFactory

        # Create messages with multiple tags
        chat = ChatFactory(team=team)
        input_message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test input")
        tag1 = Tag.objects.create(name="important", team=team, is_system_tag=False)
        tag2 = Tag.objects.create(name="urgent", team=team, is_system_tag=False)
        tag3 = Tag.objects.create(name="review", team=team, is_system_tag=False)
        input_message.add_tag(tag1, team)
        input_message.add_tag(tag2, team)
        input_message.add_tag(tag3, team)

        trace.input_message = input_message
        trace.save()

        queryset = Trace.objects.filter(team=team)

        # Test ALL_OF operator - all tags present
        value = json.dumps(["important", "urgent"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.ALL_OF, value)
        assert trace in result

        # Test ALL_OF operator - one tag missing
        value = json.dumps(["important", "missing"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.ALL_OF, value)
        assert trace not in result

    def test_message_tags_filter_excludes(self, trace, team):
        """Test message tags filter with EXCLUDES operator."""
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.utils.factories.experiment import ChatFactory

        # Create message with tags
        chat = ChatFactory(team=team)
        input_message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test input")
        tag1 = Tag.objects.create(name="important", team=team, is_system_tag=False)
        Tag.objects.create(name="urgent", team=team, is_system_tag=False)
        input_message.add_tag(tag1, team)

        trace.input_message = input_message
        trace.save()

        queryset = Trace.objects.filter(team=team)

        # Test EXCLUDES operator - tag not present (should include)
        value = json.dumps(["urgent", "other"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.EXCLUDES, value)
        assert trace in result

        # Test EXCLUDES operator - tag present (should exclude)
        value = json.dumps(["important", "other"])
        result = self._create_filter_and_apply(queryset, "message_tags", Operators.EXCLUDES, value)
        assert trace not in result
