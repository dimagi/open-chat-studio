import pytest
from django.core.management import call_command

from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.traces import TraceFactory


def _make_trace(session, **kwargs):
    return TraceFactory.create(
        session=session,
        experiment=session.experiment,
        team=session.team,
        participant=session.participant,
        **kwargs,
    )


def _make_message(session, trace_id, message_type=ChatMessageType.AI, **kwargs):
    metadata = kwargs.pop(
        "metadata",
        {"trace_info": [{"trace_id": trace_id, "trace_url": "http://example.com", "trace_provider": "ocs"}]},
    )
    return ChatMessageFactory.create(
        chat=session.chat,
        message_type=message_type,
        content="a bot message",
        metadata=metadata,
        **kwargs,
    )


@pytest.fixture()
def session():
    return ExperimentSessionFactory.create()


@pytest.mark.django_db()
class TestBackfillTraceOutputMessage:
    def _call_command(self, **kwargs):
        call_command("backfill_trace_output_message", **kwargs)

    def test_links_ai_message_to_trace(self, session):
        trace = _make_trace(session)
        message = _make_message(session, trace.id)

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message == message

    def test_ignores_human_messages(self, session):
        trace = _make_trace(session)
        _make_message(session, trace.id, message_type=ChatMessageType.HUMAN)

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message is None

    def test_ignores_messages_for_other_traces(self, session):
        trace = _make_trace(session)
        other_trace = _make_trace(session)
        message = _make_message(session, other_trace.id)

        self._call_command()

        trace.refresh_from_db()
        other_trace.refresh_from_db()
        assert trace.output_message is None
        assert other_trace.output_message == message

    def test_does_not_change_traces_that_are_already_linked(self, session):
        trace = _make_trace(session)
        linked_message = _make_message(session, trace.id)
        trace.output_message = linked_message
        trace.save()
        # a second message referencing the same trace should not steal the link
        _make_message(session, trace.id)

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message == linked_message

    def test_picks_latest_message_when_multiple_match(self, session):
        trace = _make_trace(session)
        _make_message(session, trace.id)
        latest = _make_message(session, trace.id)

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message == latest

    def test_supports_legacy_dict_trace_info_format(self, session):
        trace = _make_trace(session)
        message = _make_message(
            session,
            trace.id,
            metadata={"trace_info": {"trace_id": trace.id}, "trace_provider": "ocs"},
        )

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message == message

    def test_does_not_match_string_trace_ids_from_external_providers(self, session):
        # Langfuse entries store their own trace id as a string; a string that happens
        # to equal the OCS trace pk must not match.
        trace = _make_trace(session)
        _make_message(
            session,
            trace.id,
            metadata={"trace_info": [{"trace_id": str(trace.id), "trace_provider": "langfuse"}]},
        )

        self._call_command()

        trace.refresh_from_db()
        assert trace.output_message is None

    def test_dry_run_does_not_apply_changes(self, session):
        trace = _make_trace(session)
        _make_message(session, trace.id)

        self._call_command(dry_run=True)

        trace.refresh_from_db()
        assert trace.output_message is None

    def test_team_filter(self, session):
        other_session = ExperimentSessionFactory.create()
        trace = _make_trace(session)
        other_trace = _make_trace(other_session)
        message = _make_message(session, trace.id)
        _make_message(other_session, other_trace.id)

        self._call_command(team_slug=session.team.slug)

        trace.refresh_from_db()
        other_trace.refresh_from_db()
        assert trace.output_message == message
        assert other_trace.output_message is None
