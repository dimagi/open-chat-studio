from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessageType
from apps.evaluations.forms import EvaluationConfigForm, EvaluationDatasetEditForm, EvaluationDatasetForm
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMessage, EvaluationMode
from apps.evaluations.tasks import create_dataset_from_sessions_task
from apps.evaluations.utils import make_session_evaluation_messages
from apps.trace.models import TraceStatus
from apps.utils.factories.evaluations import EvaluationDatasetFactory, EvaluatorFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
class TestSessionModeEvaluationMessage:
    def test_str_with_empty_input_output(self):
        """Session-mode messages have empty input/output dicts."""
        msg = EvaluationMessage(
            input={},
            output={},
            history=[
                {"message_type": "human", "content": "Hello there"},
                {"message_type": "ai", "content": "Hi!"},
            ],
        )
        result = str(msg)
        assert result == "Session evaluation"

    def test_str_with_normal_input_output(self):
        """Message-mode messages should still work as before."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
        )
        result = str(msg)
        assert "Hello" in result
        assert "Hi!" in result

    def test_as_result_dict_includes_participant_data_and_session_state(self):
        """as_result_dict should include participant_data and session_state."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
            context={"current_datetime": "2025-01-01"},
            history=[],
            metadata={"session_id": "abc"},
            participant_data={"name": "John"},
            session_state={"step": 1},
        )
        result = msg.as_result_dict()
        assert result["participant_data"] == {"name": "John"}
        assert result["session_state"] == {"step": 1}
        assert result["input"] == {"content": "Hello", "role": "human"}
        assert result["output"] == {"content": "Hi!", "role": "ai"}
        assert result["context"] == {"current_datetime": "2025-01-01"}
        assert result["history"] == []
        assert result["metadata"] == {"session_id": "abc"}


@pytest.mark.django_db()
class TestMakeSessionEvaluationMessages:
    def test_happy_path_multi_turn_session(self):
        """Session with N turns produces one EvaluationMessage with full history."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team, state={"step": 3})
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi there!")
        human_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="How are you?")

        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_1,
            output_message=ai_1,
            status=TraceStatus.SUCCESS,
            duration=100,
            participant_data={"name": "Alice"},
            session_state={"step": 2},
        )

        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="I'm doing well!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_2,
            output_message=ai_2,
            status=TraceStatus.SUCCESS,
            duration=100,
            participant_data={"name": "Alice", "visits": 3},
            session_state={"step": 3},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        msg = result[0]
        assert msg.input == {}
        assert msg.output == {}
        assert len(msg.history) == 4
        assert msg.history[0]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[0]["content"] == "Hello"
        assert msg.history[1]["message_type"] == ChatMessageType.AI
        assert msg.history[1]["content"] == "Hi there!"
        assert msg.history[2]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[2]["content"] == "How are you?"
        assert msg.history[3]["message_type"] == ChatMessageType.AI
        assert msg.history[3]["content"] == "I'm doing well!"
        assert msg.participant_data == {"name": "Alice", "visits": 3}
        assert msg.session_state == {"step": 3}
        assert msg.metadata["session_id"] == str(session.external_id)
        assert msg.metadata["experiment_id"] == str(session.experiment.public_id)
        assert msg.metadata["created_mode"] == "clone"
        assert msg.session == session
        assert msg.input_chat_message is None
        assert msg.expected_output_chat_message is None

    def test_single_turn_session(self):
        """Session with one human-AI pair still produces one message."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2

    def test_orphaned_last_human_message(self):
        """Session ending with human message (no AI response)."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Bye")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 3
        assert result[0].input == {}
        assert result[0].output == {}
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_human_only_session(self):
        """Session with only human messages."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Anyone there?")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_empty_session(self):
        """Session with no messages produces no EvaluationMessage."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 0

    def test_participant_data_from_production_shaped_trace(self):
        """participant_data comes from the trace of the last completed turn.

        Production wires Trace.input_message to the *human* message
        (apps/channels/stages/core.py) and Trace.output_message to the AI message
        (apps/chat/bots.py). A trace shaped that way must still supply
        participant_data. session_state comes from the session itself, since
        Trace.session_state is only the start-of-turn snapshot.
        """
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team, state={"final": True})
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_1,
            output_message=ai_1,
            status=TraceStatus.SUCCESS,
            duration=100,
            participant_data={"version": 1},
            session_state={"first": True},
        )

        human_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="More")
        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Sure!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_2,
            output_message=ai_2,
            status=TraceStatus.SUCCESS,
            duration=100,
            participant_data={"version": 2},
            session_state={"first": False},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 4
        assert result[0].participant_data == {"version": 2}
        # From ExperimentSession.state, not the trace snapshot.
        assert result[0].session_state == {"final": True}

    @pytest.mark.parametrize(
        ("newest_trace_kwargs", "case_description"),
        [
            pytest.param(
                {"status": TraceStatus.PENDING, "with_output_message": True},
                "a trace still marked PENDING even though its reply row exists",
                id="pending_with_output_message",
            ),
            pytest.param(
                {"status": TraceStatus.SUCCESS, "with_output_message": False},
                "a turn that never produced a reply",
                id="no_output_message",
            ),
        ],
    )
    def test_incomplete_newest_trace_is_ignored(self, newest_trace_kwargs, case_description):
        """An in-flight turn must not supply participant_data; the last completed turn wins.

        Both discriminators are exercised independently: `status != PENDING` and
        `output_message IS NOT NULL`. Dropping either filter has to fail one of these cases.
        """
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_1,
            output_message=ai_1,
            status=TraceStatus.SUCCESS,
            duration=100,
            participant_data={"version": 1},
            session_state={"first": True},
        )
        # A newer turn that is still running.
        human_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="More")
        in_flight_output = None
        if newest_trace_kwargs["with_output_message"]:
            in_flight_output = ChatMessageFactory.create(
                chat=chat, message_type=ChatMessageType.AI, content="Working on it"
            )
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=human_2,
            output_message=in_flight_output,
            status=newest_trace_kwargs["status"],
            duration=0,
            participant_data={"version": "in-flight"},
            session_state={"first": "in-flight"},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert result[0].participant_data == {"version": 1}

    def test_multiple_traces_on_one_message_do_not_duplicate_history(self):
        """Two traces referencing the same ChatMessage must not duplicate it in history.

        Trace.input_message is a reverse FK (related_name="input_message_trace"), so
        joining through it emits one row per Trace and inflates the conversation.
        """
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        # The same turn traced twice (e.g. a retried generation).
        for version in (1, 2):
            TraceFactory.create(
                team=team,
                experiment=session.experiment,
                session=session,
                participant=session.participant,
                input_message=human_1,
                output_message=ai_1,
                status=TraceStatus.SUCCESS,
                duration=100,
                participant_data={"version": version},
                session_state={"attempt": version},
            )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2
        assert [entry["content"] for entry in result[0].history] == ["Hello", "Hi!"]
        # Tie-break is the most recent trace (-timestamp, -id).
        assert result[0].participant_data == {"version": 2}

    def test_multiple_sessions(self):
        """Multiple sessions produce one EvaluationMessage each."""
        team = TeamFactory.create()
        session_1 = ExperimentSessionFactory.create(team=team)
        session_2 = ExperimentSessionFactory.create(team=team)

        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.HUMAN, content="S1 Hello")
        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.AI, content="S1 Hi!")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.HUMAN, content="S2 Hello")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.AI, content="S2 Hi!")

        result = make_session_evaluation_messages([session_1.external_id, session_2.external_id])

        assert len(result) == 2
        session_ids = {msg.metadata["session_id"] for msg in result}
        assert str(session_1.external_id) in session_ids
        assert str(session_2.external_id) in session_ids

    def test_metadata_structure(self):
        """Verify metadata has session_id, experiment_id, and created_mode."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        metadata = result[0].metadata
        assert metadata["session_id"] == str(session.external_id)
        assert metadata["experiment_id"] == str(session.experiment.public_id)
        assert metadata["created_mode"] == "clone"


@pytest.mark.django_db()
class TestSessionModeDuplicateDetection:
    def test_duplicate_session_skipped_via_fk(self):
        """Re-importing the same session is skipped using the session FK PK (task's actual dedup path)."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        dataset = EvaluationDataset.objects.create(
            team=team, name="Dedup Test Dataset", evaluation_mode=EvaluationMode.SESSION
        )

        # First import via task
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            result1 = create_dataset_from_sessions_task.delay(dataset.id, team.id, [session.external_id]).get()

        assert result1["created_count"] == 1
        assert result1["duplicates_skipped"] == 0

        # Second import of same session — task should detect duplicate via FK
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            result2 = create_dataset_from_sessions_task.delay(dataset.id, team.id, [session.external_id]).get()

        assert result2["created_count"] == 0
        assert result2["duplicates_skipped"] == 1
        assert dataset.messages.count() == 1
        assert dataset.status == DatasetCreationStatus.COMPLETED


@pytest.mark.django_db()
class TestDatasetFormEvaluationMode:
    def test_create_form_includes_evaluation_mode_field(self):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        form = EvaluationDatasetForm(team=team, user=user)
        assert "evaluation_mode" in form.fields

    def test_create_form_session_mode_only_allows_clone(self):
        """When mode is session, only clone is valid."""
        team = TeamWithUsersFactory.create()
        user = team.members.first()

        form = EvaluationDatasetForm(
            team=team,
            user=user,
            data={
                "name": "Test Session Dataset",
                "evaluation_mode": "session",
                "mode": "manual",
                "messages_json": (
                    '[{"human": {"content": "Hi", "role": "human"},'
                    ' "ai": {"content": "Hello", "role": "ai"}, "context": {}}]'
                ),
            },
        )
        assert not form.is_valid()
        assert "mode" in form.errors

    def test_edit_form_excludes_evaluation_mode_field(self):
        """evaluation_mode is immutable — excluded from edit form."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        form = EvaluationDatasetEditForm(team=team, instance=dataset)
        assert "evaluation_mode" not in form.fields

    def test_edit_form_session_mode_uses_session_clone_task(self):
        """Editing a session-mode dataset calls _save_sessions_clone, not _save_session_messages_clone."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")

        form = EvaluationDatasetEditForm(
            team=team,
            instance=dataset,
            data={
                "name": dataset.name,
                "mode": "clone",
                "session_ids": str(session.external_id),
            },
        )
        assert form.is_valid(), form.errors

        with (
            patch.object(form, "_save_sessions_clone") as mock_session_clone,
            patch.object(form, "_save_session_messages_clone") as mock_clone,
        ):
            form.save()

        mock_session_clone.assert_called_once()
        mock_clone.assert_not_called()

    @pytest.mark.parametrize("evaluation_mode", ["session", "message"])
    def test_create_form_allows_zero_sessions(self, evaluation_mode):
        """Either mode may be created empty and filled later — session mode by an auto-population
        rule, either mode from the Add Sessions page."""
        team = TeamWithUsersFactory.create()
        user = team.members.first()

        form = EvaluationDatasetForm(
            team=team,
            user=user,
            data={
                "name": "Empty Dataset",
                "evaluation_mode": evaluation_mode,
                "mode": "clone",
                "session_ids": "",
            },
        )
        assert form.is_valid(), form.errors

    @pytest.mark.parametrize(
        ("evaluation_mode", "task_name"),
        [
            ("session", "create_dataset_from_sessions_task"),
            ("message", "create_dataset_from_session_messages_task"),
        ],
    )
    def test_create_form_with_zero_sessions_completes_instead_of_pending(self, evaluation_mode, task_name):
        """No clone job is dispatched for an empty selection, so nothing else would ever move the
        dataset off PENDING — it must be closed out at save time."""
        team = TeamWithUsersFactory.create()
        user = team.members.first()

        form = EvaluationDatasetForm(
            team=team,
            user=user,
            data={
                "name": "Empty Dataset To Complete",
                "evaluation_mode": evaluation_mode,
                "mode": "clone",
                "session_ids": "",
            },
        )
        assert form.is_valid(), form.errors
        form.instance.team = team  # the view sets these before saving
        form.instance.created_by = user

        with patch(f"apps.evaluations.forms.{task_name}.delay") as mock_delay:
            dataset = form.save()

        mock_delay.assert_not_called()
        dataset.refresh_from_db()
        assert dataset.status == DatasetCreationStatus.COMPLETED

    def test_edit_form_message_mode_uses_message_clone_task(self):
        """Editing a message-mode dataset calls _save_session_messages_clone, not _save_sessions_clone."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="message")

        form = EvaluationDatasetEditForm(
            team=team,
            instance=dataset,
            data={
                "name": dataset.name,
                "mode": "clone",
                "session_ids": str(session.external_id),
            },
        )
        assert form.is_valid(), form.errors

        with (
            patch.object(form, "_save_sessions_clone") as mock_session_clone,
            patch.object(form, "_save_session_messages_clone") as mock_clone,
        ):
            form.save()

        mock_clone.assert_called_once()
        mock_session_clone.assert_not_called()


@pytest.mark.django_db()
class TestEvalConfigFormModeValidation:
    def test_mismatched_modes_rejected(self):
        """Dataset evaluation_mode must match all evaluator evaluation_modes."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode="message")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [evaluator.id],
            },
        )
        assert not form.is_valid()
        assert "evaluators" in form.errors

    def test_matching_modes_accepted(self):
        """Same evaluation_mode on dataset and evaluators is valid."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode="session")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [evaluator.id],
            },
        )
        assert form.is_valid(), form.errors

    def test_session_mode_dataset_blocks_run_generation(self):
        """run_generation must be rejected for session-mode datasets server-side."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode="session")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [evaluator.id],
                "run_generation": True,
            },
        )
        assert not form.is_valid()
        assert "evaluators" in form.errors

    def test_mismatched_message_evaluator_with_session_dataset(self):
        """A message evaluator cannot be used with a session dataset."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        session_evaluator = EvaluatorFactory.create(team=team, evaluation_mode="session", name="Session Eval")
        message_evaluator = EvaluatorFactory.create(team=team, evaluation_mode="message", name="Message Eval")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [session_evaluator.id, message_evaluator.id],
            },
        )
        assert not form.is_valid()
        assert "evaluators" in form.errors
        assert "Message Eval" in form.errors["evaluators"][0]

    def test_evaluators_widget_renders_data_evaluation_mode_attributes(self):
        """Each evaluator checkbox should have data-evaluation-mode for Alpine.js UI filtering."""
        team = TeamFactory.create()
        EvaluatorFactory.create(
            team=team, evaluation_mode=EvaluationMode.MESSAGE, name="Message Eval", type="LlmEvaluator"
        )
        EvaluatorFactory.create(
            team=team, evaluation_mode=EvaluationMode.SESSION, name="Session Eval", type="LlmEvaluator"
        )

        form = EvaluationConfigForm(team=team)
        rendered = str(form["evaluators"])

        assert 'data-evaluation-mode="message"' in rendered
        assert 'data-evaluation-mode="session"' in rendered
