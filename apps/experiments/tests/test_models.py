from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
import time_machine
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone
from time_machine import travel

from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.actions import ScheduleTriggerAction
from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.experiments.models import (
    ConsentForm,
    Experiment,
    ExperimentRoute,
    ExperimentSession,
    ParticipantData,
    SafetyLayer,
    SyntheticVoice,
)
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy
from apps.service_providers.tracing import TraceInfo
from apps.trace.models import Trace, TraceStatus
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.events import (
    EventActionFactory,
    ScheduledMessageFactory,
    StaticTriggerFactory,
    TimeoutTriggerFactory,
)
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
    SourceMaterialFactory,
    SurveyFactory,
    SyntheticVoiceFactory,
)
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


class TestSyntheticVoice:
    @django_db_with_data()
    def test_team_scoped_services(self):
        assert [SyntheticVoice.OpenAIVoiceEngine] == SyntheticVoice.TEAM_SCOPED_SERVICES

    @django_db_with_data()
    def test_get_for_team_returns_all_general_services(self):
        """General services are those not included in SyntheticVoice.TEAM_SCOPED_SERVICES"""
        voices_queryset = SyntheticVoice.get_for_team(team=None)
        assert voices_queryset.count() == SyntheticVoice.objects.count()

    @django_db_with_data()
    def test_get_for_team_excludes_service(self):
        voices_queryset = SyntheticVoice.get_for_team(team=None, exclude_services=[SyntheticVoice.AWS])
        services = set(voices_queryset.values_list("service", flat=True))
        assert services == {SyntheticVoice.OpenAI, SyntheticVoice.Azure}

    @django_db_with_data()
    def test_get_for_team_do_not_include_other_team_exclusive_voices(self):
        """Tests that `get_for_team` returns both general and team exclusive synthetic voices. Exclusive synthetic
        voices are those whose service is one of SyntheticVoice.TEAM_SCOPED_SERVICES
        """
        all_services = {
            SyntheticVoice.AWS,
            SyntheticVoice.OpenAI,
            SyntheticVoice.Azure,
            SyntheticVoice.OpenAIVoiceEngine,
        }
        # Let's setup two providers belonging to different teams
        team1 = TeamFactory()
        team2 = TeamFactory()

        # Create synthetic voices with providers from different teams. They should be exclusive to their teams
        voice1 = SyntheticVoiceFactory(
            voice_provider=VoiceProviderFactory(team=team1), service=SyntheticVoice.OpenAIVoiceEngine
        )
        voice2 = SyntheticVoiceFactory(
            voice_provider=VoiceProviderFactory(team=team2), service=SyntheticVoice.OpenAIVoiceEngine
        )

        # If a voice form another team's service outisde of TEAM_SCOPED_SERVICES happens to have a provider, we
        # should not match on that
        voice3 = SyntheticVoiceFactory(voice_provider=VoiceProviderFactory(team=team2), service=SyntheticVoice.AWS)

        # Assert exclusivity
        voices_queryset = SyntheticVoice.get_for_team(team1)
        services = set(voices_queryset.values_list("service", flat=True))
        assert services == all_services
        assert voice2 not in voices_queryset
        assert voice3 not in voices_queryset

        voices_queryset = SyntheticVoice.get_for_team(team2)
        assert set(voices_queryset.values_list("service", flat=True)) == all_services
        assert voice1 not in voices_queryset
        assert voice3 in voices_queryset

        # Although voice1 belongs to team1, if we exclude its service, it should not be returned
        voices_queryset = SyntheticVoice.get_for_team(team1, exclude_services=[SyntheticVoice.OpenAIVoiceEngine])
        services = set(voices_queryset.values_list("service", flat=True))
        assert services == {SyntheticVoice.AWS, SyntheticVoice.OpenAI, SyntheticVoice.Azure}
        assert voice1 not in voices_queryset


@pytest.mark.django_db()
class TestExperimentSession:
    def _construct_event_action(self, time_period: TimePeriod, experiment_id: int, frequency=1, repetitions=1) -> tuple:
        params = self._get_params(experiment_id, time_period, frequency, repetitions)
        return EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER), params

    def _get_params(self, experiment_id: int, time_period: TimePeriod = TimePeriod.DAYS, frequency=1, repetitions=1):
        return {
            "name": "Test",
            "time_period": time_period,
            "frequency": frequency,
            "repetitions": repetitions,
            "prompt_text": "hi",
            "experiment_id": experiment_id,
        }

    @travel("2024-01-01", tick=False)
    def test_get_participant_scheduled_messages_custom_params(self):
        session = ExperimentSessionFactory()
        experiment = session.experiment
        event_action, params = self._construct_event_action(time_period=TimePeriod.DAYS, experiment_id=experiment.id)
        participant = session.participant
        message1 = ScheduledMessageFactory(
            experiment=experiment,
            team=session.team,
            participant=participant,
            action=event_action,
        )
        message2 = ScheduledMessageFactory(
            experiment=experiment,
            team=session.team,
            participant=participant,
            custom_schedule_params=params,
            action=None,
        )

        assert len(participant.get_schedules_for_experiment(experiment.id)) == 2

        def _make_string(message, is_system):
            return (
                f"{message.name} (Message id={message.external_id}, message={message.prompt_text}): "
                "One-off reminder. Next trigger is at Tuesday, 02 January 2024 00:00:00 UTC."
                f"{' (System)' if is_system else ''}"
            )

        scheduled_messages_str = participant.get_schedules_for_experiment(experiment.id)
        assert scheduled_messages_str[0] == _make_string(message1, True)
        assert scheduled_messages_str[1] == _make_string(message2, False)

        def _make_expected_dict(external_id):
            return {
                "name": "Test",
                "external_id": external_id,
                "frequency": 1,
                "time_period": "days",
                "repetitions": 1,
                "next_trigger_date": datetime(2024, 1, 2, tzinfo=UTC),
                "is_complete": False,
                "last_triggered_at": None,
                "total_triggers": 0,
                "triggers_remaining": 1,
                "prompt": "hi",
                "is_cancelled": False,
                "attempts": [],
            }

        expected_dict_version = [
            _make_expected_dict(message1.external_id),
            _make_expected_dict(message2.external_id),
        ]
        assert participant.get_schedules_for_experiment(experiment.id, as_dict=True) == expected_dict_version

    @pytest.mark.parametrize(
        ("repetitions", "total_triggers", "expected_triggers_remaining"),
        [
            (None, 0, 1),
            (0, 0, 1),
            (1, 0, 1),
            (1, 1, 0),
        ],
    )
    def test_get_schedules_for_experiment_as_dict(self, repetitions, total_triggers, expected_triggers_remaining):
        session = ExperimentSessionFactory()
        experiment = session.experiment
        participant = session.participant

        ScheduledMessageFactory(
            experiment=experiment,
            team=session.team,
            participant=participant,
            action=None,
            next_trigger_date=timezone.now(),
            last_triggered_at=timezone.now() if total_triggers > 0 else None,
            total_triggers=total_triggers,
            custom_schedule_params=self._get_params(experiment.id, repetitions=repetitions),
        )

        schedules = participant.get_schedules_for_experiment(experiment.id, as_dict=True)

        assert len(schedules) == 1
        schedule = schedules[0]
        assert schedule["repetitions"] == (repetitions or 0)
        assert schedule["total_triggers"] == total_triggers
        assert schedule["triggers_remaining"] == expected_triggers_remaining

    @travel("2024-01-01", tick=False)
    @pytest.mark.parametrize(
        ("time_period", "repetitions", "total_triggers", "expected"),
        [
            (
                TimePeriod.DAYS,
                None,
                0,
                "Test (Message id={message.external_id}, message=hi): One-off reminder. {next_trigger}",
            ),
            (
                TimePeriod.DAYS,
                0,
                0,
                "Test (Message id={message.external_id}, message=hi): One-off reminder. {next_trigger}",
            ),
            (
                TimePeriod.DAYS,
                1,
                0,
                "Test (Message id={message.external_id}, message=hi): One-off reminder. {next_trigger}",
            ),
            (
                TimePeriod.DAYS,
                1,
                1,
                "Test (Message id={message.external_id}, message=hi): One-off reminder. Complete.",
            ),
            (
                TimePeriod.DAYS,
                2,
                1,
                "Test (Message id={message.external_id}, message=hi): Every 1 days, 2 times. {next_trigger}",
            ),
            (
                TimePeriod.DAYS,
                2,
                2,
                "Test (Message id={message.external_id}, message=hi): Every 1 days, 2 times. Complete.",
            ),
            (
                TimePeriod.WEEKS,
                2,
                1,
                "Test (Message id={message.external_id}, message=hi): Every 1 weeks on Monday, 2 times. {next_trigger}",
            ),
            (
                TimePeriod.MONTHS,
                2,
                1,
                "Test (Message id={message.external_id}, message=hi): Every 1 months, 2 times. {next_trigger}",
            ),
        ],
    )
    def test_get_schedules_for_experiment_as_string(self, time_period, repetitions, total_triggers, expected):
        session = ExperimentSessionFactory()
        experiment = session.experiment
        participant = session.participant

        message = ScheduledMessageFactory(
            experiment=experiment,
            team=session.team,
            participant=participant,
            action=None,
            next_trigger_date=timezone.now(),
            last_triggered_at=timezone.now() if total_triggers > 0 else None,
            total_triggers=total_triggers,
            custom_schedule_params=self._get_params(experiment.id, repetitions=repetitions, time_period=time_period),
        )

        schedules = participant.get_schedules_for_experiment(experiment.id, as_dict=False)

        assert len(schedules) == 1
        schedule = schedules[0]
        next_trigger = "Next trigger is at Monday, 01 January 2024 00:00:00 UTC."
        assert schedule == expected.format(message=message, next_trigger=next_trigger)

    def test_get_participant_scheduled_messages_includes_child_experiments(self):
        session = ExperimentSessionFactory()
        team = session.team
        participant = session.participant
        session2 = ExperimentSessionFactory(experiment__team=team, participant=participant)
        event_action = event_action, params = self._construct_event_action(
            time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
        )
        ScheduledMessageFactory(experiment=session.experiment, team=team, participant=participant, action=event_action)
        ScheduledMessageFactory(experiment=session2.experiment, team=team, participant=participant, action=event_action)
        ExperimentRoute.objects.create(team=team, parent=session.experiment, child=session2.experiment, keyword="test")

        assert len(participant.get_schedules_for_experiment(session2.experiment_id)) == 1
        assert len(participant.get_schedules_for_experiment(session.experiment_id)) == 2

    @pytest.mark.parametrize("use_custom_experiment", [False, True])
    @patch.object(ExperimentSession, "ad_hoc_bot_message")
    def test_scheduled_message_experiment(self, mock_ad_hoc, use_custom_experiment):
        """ScheduledMessages should use the experiment specified in the linked action's params"""
        custom_experiment = ExperimentFactory() if use_custom_experiment else None
        mock_ad_hoc.return_value = {}
        session = ExperimentSessionFactory()
        event_action_kwargs = {"time_period": TimePeriod.DAYS, "experiment_id": session.experiment.id}
        if custom_experiment:
            event_action_kwargs["experiment_id"] = custom_experiment.id

        event_action, params = self._construct_event_action(**event_action_kwargs)
        trigger_action = ScheduleTriggerAction()
        trigger_action.invoke(session, action=event_action)

        message = ScheduledMessage.objects.get(action=event_action)
        message.participant.get_latest_session = lambda *args, **kwargs: session
        message.safe_trigger()

        experiment_used = session.ad_hoc_bot_message.call_args_list[0].kwargs["use_experiment"]
        if use_custom_experiment:
            assert experiment_used == custom_experiment
        else:
            assert experiment_used == session.experiment

    @pytest.mark.parametrize(
        ("repetitions", "total_triggers", "end_date", "expected"),
        [
            pytest.param(None, 0, None, False, id="null_reps_not_triggered"),
            pytest.param(None, 1, None, True, id="null_reps_triggered"),
            pytest.param(0, 0, None, False, id="zero_reps_not_triggered"),
            pytest.param(0, 1, None, True, id="zero_reps_triggered"),
            pytest.param(3, 2, None, False, id="reps_not_met"),
            pytest.param(3, 3, None, True, id="reps_met"),
            pytest.param(1, 0, timezone.now() - timezone.timedelta(days=1), True, id="past_end_date"),
            pytest.param(1, 0, timezone.now() + timezone.timedelta(days=1), False, id="before_end_date"),
        ],
    )
    def test_should_mark_complete(self, repetitions, total_triggers, end_date, expected):
        scheduled_message = ScheduledMessage(
            custom_schedule_params={"repetitions": repetitions},
            total_triggers=total_triggers,
            end_date=end_date,
        )
        assert scheduled_message._should_mark_complete() == expected

    def test_get_participant_data_name(self):
        participant = ParticipantFactory()
        session = ExperimentSessionFactory(participant=participant, team=participant.team)
        data = {"first_name": "Jimmy"}
        data_proxy = ParticipantDataProxy({"participant_data": data}, session)
        data = data_proxy.get()
        assert data == {
            "name": participant.name,
            "first_name": "Jimmy",
        }

        data["name"] = "James Newman"

        data_proxy = ParticipantDataProxy({"participant_data": data}, session)
        data = data_proxy.get()
        assert data == {
            "name": "James Newman",
            "first_name": "Jimmy",
        }

    @pytest.mark.parametrize("fail_silently", [True, False])
    @patch("apps.chat.channels.ChannelBase.from_experiment_session")
    @patch.object(ExperimentSession, "_bot_prompt_for_user")
    def test_ad_hoc_message(self, mock_bot_prompt, from_experiment_session, fail_silently, experiment_session):
        mock_channel = Mock()
        mock_channel.send_message_to_user = Mock()
        if not fail_silently:
            mock_channel.send_message_to_user.side_effect = Exception("Cannot send message")
        from_experiment_session.return_value = mock_channel
        mock_bot_prompt.return_value = "We're testing"

        def _test():
            experiment_session.ad_hoc_bot_message(
                "Tell the user we're testing", TraceInfo(name="test"), fail_silently=fail_silently
            )
            call = mock_channel.send_message_to_user.mock_calls[0]
            assert call.args[0] == "We're testing"

        if not fail_silently:
            with pytest.raises(Exception, match="Cannot send message"):
                _test()
        else:
            _test()

    @patch("apps.chat.channels.ChannelBase.from_experiment_session")
    @patch.object(ExperimentSession, "_bot_prompt_for_user")
    def test_ad_hoc_message_transaction_rollback(self, mock_bot_prompt, from_experiment_session, experiment_session):
        """Test that the @transaction.atomic() decorator on ad_hoc_bot_message
        rolls back database changes when an exception occurs."""
        # Set up initial state
        initial_message_count = ChatMessage.objects.filter(chat=experiment_session.chat).count()

        # Mock the bot to return a message
        mock_bot_prompt.return_value = "Test message"
        # Mock channel to create a message then raise an exception on send
        mock_channel = Mock()
        from_experiment_session.return_value = mock_channel

        def mock_send_with_db_change(message):
            # Simulate creating a chat message before the exception
            # This should be rolled back due to the transaction decorator
            ChatMessage.objects.create(
                message_type=ChatMessageType.AI,
                content="Message created before exception",
                chat=experiment_session.chat,
            )
            raise Exception("Send failed - should rollback")

        mock_channel.send_message_to_user = mock_send_with_db_change

        # Call ad_hoc_bot_message with fail_silently=False so exception propagates
        with pytest.raises(Exception, match="Send failed - should rollback"):
            experiment_session.ad_hoc_bot_message(
                "Tell the user we're testing", TraceInfo(name="test"), fail_silently=False
            )

        # Verify that the database changes were rolled back
        final_message_count = ChatMessage.objects.filter(chat=experiment_session.chat).count()
        assert final_message_count == initial_message_count, "Transaction should have rolled back the message creation"

    @pytest.mark.parametrize("participant_data_injected", [True, False])
    def test_requires_participant_data(self, participant_data_injected):
        prompt = "data: {participant_data}" if participant_data_injected else "data"

        def _test_pipline(node_type, params):
            pipeline = PipelineFactory()
            NodeFactory(pipeline=pipeline, type=node_type, params=params)
            session = ExperimentSessionFactory(experiment__pipeline=pipeline)
            assert session.requires_participant_data() == participant_data_injected

        # Case 3 - Pipeline Assistant Node
        assistant = OpenAiAssistantFactory(instructions=prompt)
        _test_pipline("AssistantNode", params={"assistant_id": assistant.id})

        # Case 4 - Pipeline LLMResponseWithPrompt Node
        _test_pipline("LLMResponseWithPrompt", params={"prompt": prompt})

        # Case 5 - Pipeline Router Node
        _test_pipline("RouterNode", params={"prompt": prompt})


class TestParticipant:
    @pytest.mark.django_db()
    def test_update_memory_updates_all_data(self):
        participant = ParticipantFactory()
        team = participant.team
        sessions = ExperimentSessionFactory.create_batch(3, participant=participant, team=team, experiment__team=team)
        # let the participant be linked to an experiment in another team as well. That experiment should be unaffected
        ExperimentSessionFactory(participant=participant)
        existing_data_obj = ParticipantData.objects.create(
            team=team,
            experiment=sessions[0].experiment,
            data={"first_name": "Jack", "last_name": "Turner"},
            participant=participant,
        )
        participant.update_memory({"first_name": "Elizabeth"}, experiment=sessions[1].experiment)
        participant_data_query = ParticipantData.objects.filter(team=team, participant=participant)

        # expect 2 objects, 1 that was created before and 1 that was created in `update_memory`
        assert participant_data_query.count() == 2
        for p_data in participant_data_query.all():
            if p_data == existing_data_obj:
                assert p_data.data == {"first_name": "Elizabeth", "last_name": "Turner"}
            else:
                assert p_data.data == {"first_name": "Elizabeth"}


@pytest.mark.django_db()
class TestSafetyLayerVersioning:
    def test_create_new_safety_layer_version(self):
        original = SafetyLayer.objects.create(
            prompt_text="Is this message safe?", team=TeamFactory(), prompt_to_bot="Unsafe reply"
        )
        new_version = original.create_new_version()
        original.refresh_from_db()
        assert original.working_version is None
        assert new_version != original
        assert new_version.working_version == original
        assert new_version.prompt_text == original.prompt_text
        assert new_version.prompt_to_bot == original.prompt_to_bot
        assert new_version.team == original.team


@pytest.mark.django_db()
class TestSourceMaterialVersioning:
    def test_create_new_source_material_version(self):
        original = SourceMaterialFactory()
        new_version = original.create_new_version()
        original.refresh_from_db()
        assert original.working_version is None
        _compare_models(original, new_version, expected_changed_fields=["id", "working_version_id"])


@pytest.mark.django_db()
class TestExperimentRouteVersioning:
    def _setup_route(self):
        parent_exp = ExperimentFactory(pipeline=None, prompt_text="You are a helpful assistant")
        team = parent_exp.team
        child_exp = ExperimentFactory(team=team, pipeline=None, prompt_text="You are a helpful assistant")
        exp_route = ExperimentRoute.objects.create(team=team, parent=parent_exp, child=child_exp, keyword="testing")
        return child_exp, exp_route

    def test_child_without_versions(self):
        """
        When the child doesn't have a version then we expect a new version to be created for the new route version.
        """
        child_exp, exp_route = self._setup_route()
        new_parent = ExperimentFactory(team=exp_route.team, pipeline=None, prompt_text="You are a helpful assistant")
        route_version = exp_route.create_new_version(new_parent=new_parent)

        assert route_version.child == child_exp.latest_version

    def test_child_with_changes_since_latest_version(self):
        """
        When the child have a version, but also changed since the latest version, then we expect a new version to be
        created for the new route version.
        """
        child_exp, exp_route = self._setup_route()

        # Create a version of the child
        first_child_version = child_exp.create_new_version()
        # Changes since the last version
        child_exp.prompt_text = "New prompt"

        # This should create a new version of the child as well
        new_parent = ExperimentFactory(team=exp_route.team, pipeline=None, prompt_text="You are a helpful assistant")
        route_version = exp_route.create_new_version(new_parent=new_parent)
        assert child_exp.latest_version != first_child_version
        assert route_version.child == child_exp.latest_version

    def test_child_with_no_changes_since_latest_version(self):
        """When the child has a version and there are no changes between that version and the working version, then the
        latest version should be used for the new route version.
        """
        child_exp, exp_route = self._setup_route()
        # First create a version of the child
        child_version = child_exp.create_new_version()
        new_parent = ExperimentFactory(team=exp_route.team, pipeline=None, prompt_text="You are a helpful assistant")
        route_version = exp_route.create_new_version(new_parent=new_parent)
        assert route_version.child == child_version


@pytest.mark.django_db()
class TestExperimentRoute:
    def test_eligible_children(self):
        parent = ExperimentFactory()
        experiment_version = parent.create_new_version()
        experiment1 = ExperimentFactory(team=parent.team)
        experiment2 = ExperimentFactory(team=parent.team)

        queryset = ExperimentRoute.eligible_children(team=parent.team, parent=parent)
        assert parent not in queryset
        assert experiment_version not in queryset
        assert experiment1 in queryset
        assert experiment2 in queryset
        assert len(queryset) == 2

        queryset = ExperimentRoute.eligible_children(team=parent.team)
        assert len(queryset) == 3

    def test_compare_with_model_testcase_4(self):
        """
        The children are experiments of different families.
        """
        parent = ExperimentFactory()
        versioned_parent = parent.create_new_version()
        child1 = ExperimentFactory(team=parent.team)
        child2 = ExperimentFactory(team=parent.team)
        route = ExperimentRoute.objects.create(parent=parent, child=child1, keyword="test", team=parent.team)
        route2 = ExperimentRoute.objects.create(
            parent=versioned_parent, child=child2, keyword="test", team=parent.team, working_version=route
        )
        route1_version = route.version_details
        route1_version.compare(route2.version_details)
        changed_fields = [field.name for field in route1_version.fields if field.changed]
        assert changed_fields == ["child"]

    def _setup_route(self, keyword: str):
        parent = ExperimentFactory()
        team = parent.team
        child = ExperimentFactory(team=team)
        return ExperimentRoute.objects.create(parent=parent, child=child, keyword=keyword, team=team)

    def test_unique_parent_child_constraint_enforced(self):
        route = self._setup_route(keyword="test")
        with pytest.raises(IntegrityError, match=r'.*violates unique constraint "unique_parent_child".*'):
            ExperimentRoute.objects.create(parent=route.parent, child=route.child, keyword="testing", team=route.team)

    def test_unique_parent_child_constraint_not_enforced(self):
        """Tests the conditional unique constraint is not enforced when the previous instance is archived"""
        route = self._setup_route(keyword="test")
        parent = route.parent
        route.archive()
        ExperimentRoute.objects.create(parent=route.parent, child=route.child, keyword="testing", team=route.team)
        assert parent.child_links.count() == 1

    def test_unique_parent_keyword_condition_enforced(self):
        route = self._setup_route(keyword="test")
        other_child = ExperimentFactory(team=route.team)
        with pytest.raises(IntegrityError, match=r'.*violates unique constraint "unique_parent_keyword_condition".*'):
            ExperimentRoute.objects.create(parent=route.parent, child=other_child, keyword="test", team=route.team)

    def test_unique_parent_keyword_condition_not_enforced(self):
        """Tests the conditional unique constraint is not enforced when the previous instance is archived"""
        route = self._setup_route(keyword="test")
        parent = route.parent
        other_child = ExperimentFactory(team=route.team)
        route.archive()
        ExperimentRoute.objects.create(parent=route.parent, child=other_child, keyword="test", team=route.team)
        assert parent.child_links.count() == 1


@pytest.mark.django_db()
class TestExperimentModel:
    def test_working_experiment_cannot_be_the_default_version(self):
        with pytest.raises(ValueError, match="A working experiment cannot be a default version"):
            ExperimentFactory(is_default_version=True, working_version=None)

    def test_single_default_version_per_experiment(self):
        working_exp = ExperimentFactory()
        team = working_exp.team
        ExperimentFactory(is_default_version=True, working_version=working_exp, team=team)
        with transaction.atomic(), pytest.raises(IntegrityError, match=r'.*"unique_default_version_per_experiment".*'):
            ExperimentFactory(is_default_version=True, working_version=working_exp, team=team, version_number=2)
        ExperimentFactory(is_default_version=False, working_version=working_exp, team=team, version_number=3)

    def test_unique_version_number_per_experiment(self):
        working_exp = ExperimentFactory()
        team = working_exp.team
        ExperimentFactory(working_version=working_exp, team=team, version_number=2)
        with pytest.raises(IntegrityError, match=r'.*"unique_version_number_per_experiment".*'):
            ExperimentFactory(working_version=working_exp, team=team, version_number=2)

    @pytest.mark.parametrize("assistant_id_populated", [True, False])
    def test_get_assistant_from_pipeline(self, assistant_id_populated):
        assistant = OpenAiAssistantFactory()
        assistant_id = assistant.id if assistant_id_populated else None
        pipeline = PipelineFactory()
        NodeFactory(pipeline=pipeline, type="AssistantNode", params={"assistant_id": assistant_id})
        experiment = ExperimentFactory(pipeline=pipeline)
        expected_assistant_result = assistant if assistant_id_populated else None
        assert experiment.get_assistant() == expected_assistant_result

    def _setup_original_experiment(self):
        experiment = ExperimentFactory()
        team = experiment.team
        experiment.consent_form = ConsentForm.get_default(team)

        # Setup Safety Layers
        layer1 = SafetyLayer.objects.create(
            prompt_text="Is this message safe?", team=team, prompt_to_bot="Unsafe reply"
        )
        layer2 = SafetyLayer.objects.create(prompt_text="What about this one?", team=team, prompt_to_bot="Unsafe reply")
        experiment.safety_layers.set([layer1, layer2])

        # Setup Source material
        experiment.source_material = SourceMaterialFactory(team=team, material="material science is interesting")
        experiment.save()

        # Setup Routes - There will be versioned and working children
        versioned_child = ExperimentFactory(
            team=team, version_number=1, working_version=ExperimentFactory(version_number=2)
        )
        ExperimentRoute(team=team, parent=experiment, child=versioned_child, keyword="versioned")
        working_child = ExperimentFactory(team=team)
        ExperimentRoute(team=team, parent=experiment, child=working_child, keyword="working")

        # Setup Static Trigger
        StaticTriggerFactory(experiment=experiment)

        # Setup Timeout Trigger
        TimeoutTriggerFactory(experiment=experiment)

        # Surveys
        pre_survey = SurveyFactory(team=team)
        post_survey = SurveyFactory(team=team)
        experiment.pre_survey = pre_survey
        experiment.post_survey = post_survey

        experiment.pipeline = PipelineFactory(team=experiment.team)
        return experiment

    def test_first_version_is_automatically_the_default(self):
        experiment = ExperimentFactory()
        new_version = experiment.create_new_version()
        another_version = experiment.create_new_version()
        assert new_version.version_number == 1
        assert new_version.is_default_version

        assert another_version.version_number == 2
        assert not another_version.is_default_version

    def test_create_experiment_version(self):
        original_experiment = self._setup_original_experiment()

        assert original_experiment.version_number == 1

        new_version = original_experiment.create_new_version("tis a new version")
        original_experiment.refresh_from_db()

        assert new_version != original_experiment
        assert original_experiment.version_number == 2
        assert original_experiment.working_version is None
        assert new_version.version_number == 1
        assert new_version.is_default_version is True
        assert new_version.working_version == original_experiment
        assert new_version.version_description == "tis a new version"
        _compare_models(
            original=original_experiment,
            new=new_version,
            expected_changed_fields=[
                "id",
                "source_material",
                "public_id",
                "working_version",
                "version_number",
                "is_default_version",
                "consent_form",
                "pre_survey",
                "post_survey",
                "version_description",
                "safety_layers",
                "pipeline",
            ],
        )
        self._assert_safety_layers_are_duplicated(original_experiment, new_version)
        self._assert_source_material_is_duplicated(original_experiment, new_version)
        self._assert_routes_are_duplicated(original_experiment, new_version)
        self._assert_triggers_are_duplicated("static", original_experiment, new_version)
        self._assert_triggers_are_duplicated("timeout", original_experiment, new_version)
        self._assert_attribute_duplicated("source_material", original_experiment, new_version)
        self._assert_attribute_duplicated(
            "consent_form", original_experiment, new_version, changed_fields_extra=["is_default"]
        )
        assert original_experiment.consent_form.is_default is True
        assert new_version.consent_form.is_default is False
        self._assert_attribute_duplicated("pre_survey", original_experiment, new_version)
        self._assert_attribute_duplicated("post_survey", original_experiment, new_version)
        self._assert_pipeline_is_duplicated(original_experiment, new_version)

        another_new_version = original_experiment.create_new_version()
        original_experiment.refresh_from_db()
        assert original_experiment.version_number == 3
        assert another_new_version.version_number == 2
        assert another_new_version.is_default_version is False

    def _assert_pipeline_is_duplicated(self, original_experiment, new_version):
        assert new_version.pipeline.working_version == original_experiment.pipeline
        assert new_version.pipeline.version_number == 1
        assert original_experiment.pipeline.version_number == 2
        for node in original_experiment.pipeline.node_set.all():
            assert new_version.pipeline.node_set.filter(working_version_id=node.id).exists()

    def test_copy_attr_to_new_version(self):
        """
        Copying an attribute to the a version should only create a new version of the attribute (or related model) when
        the attribute 1. is not versioned or 2. the instance differs from the latest version of itself. If there's no
        difference between the working and latest version of the attribute, the latest version should be linked to the
        new experiment version.
        """
        original_experiment = self._setup_original_experiment()
        # Choose source_material as the attribute / related model
        original_related_instance = original_experiment.source_material

        # The original related object has not versions, so we expeect a new version to be created
        experiment_version1 = ExperimentFactory()
        original_experiment._copy_attr_to_new_version("source_material", experiment_version1)
        assert original_related_instance.versions.count() == 1
        assert experiment_version1.source_material != original_related_instance
        assert experiment_version1.source_material.working_version == original_related_instance

        # No change between the original and versioned instances, so we don't want yet another version to be made
        experiment_version2 = ExperimentFactory()
        original_experiment._copy_attr_to_new_version("source_material", experiment_version2)
        assert original_related_instance.versions.count() == 1
        # The new instance version should be the same as the previous one
        assert experiment_version2.source_material == experiment_version1.source_material

        # Changing the working instance causes a new version to be made
        original_experiment.source_material.material = "Saucy Sauceness"
        original_experiment.source_material.save()
        experiment_version3 = ExperimentFactory()
        original_experiment._copy_attr_to_new_version("source_material", experiment_version3)
        assert original_related_instance.versions.count() == 2
        assert experiment_version3.source_material != experiment_version2.source_material
        assert experiment_version3.source_material.working_version == original_experiment.source_material

    def _assert_safety_layers_are_duplicated(self, original_experiment, new_version):
        for layer in original_experiment.safety_layers.all():
            assert layer.working_version is None
            assert new_version.safety_layers.filter(working_version=layer).exists()

    def _assert_source_material_is_duplicated(self, original_experiment, new_version):
        assert new_version.source_material != original_experiment.source_material
        assert new_version.source_material.working_version == original_experiment.source_material
        assert new_version.source_material.material == original_experiment.source_material.material

    def _assert_routes_are_duplicated(self, original_experiment, new_version):
        for route in new_version.child_links.all():
            assert route.parent.working_version == original_experiment
            assert route.working_version.parent == original_experiment
            assert route.child.is_a_version is True

    def _assert_triggers_are_duplicated(self, trigger_type, original_experiment, new_version):
        assert trigger_type in ["static", "timeout"], "Unknown trigger type"
        if trigger_type == "static":
            original_triggers = original_experiment.static_triggers.all()
            copied_triggers = new_version.static_triggers.all()
        elif trigger_type == "timeout":
            original_triggers = original_experiment.timeout_triggers.all()
            copied_triggers = new_version.timeout_triggers.all()

        assert len(copied_triggers) == len(original_triggers)

        for copied_trigger in copied_triggers:
            assert copied_trigger.working_version is not None
            assert copied_trigger.working_version in original_triggers
            _compare_models(
                original=copied_trigger.working_version,
                new=copied_trigger,
                expected_changed_fields=["id", "action", "working_version", "experiment"],
            )

    def _assert_attribute_duplicated(
        self, attr_name, original_experiment, new_version, changed_fields_extra: list | None = None
    ):
        default_fields_that_differ = ["id", "working_version"]
        if changed_fields_extra:
            default_fields_that_differ.extend(changed_fields_extra)
        _compare_models(
            original=getattr(original_experiment, attr_name),
            new=getattr(new_version, attr_name),
            expected_changed_fields=default_fields_that_differ,
        )

    def test_get_version(self, experiment):
        """Test that we are able to find a specific experiment version using any experiment in the version family"""
        first_version = experiment.create_new_version()
        second_version = experiment.create_new_version(make_default=True)
        experiment.refresh_from_db()
        first_version.refresh_from_db()

        # Get the default version
        assert experiment.get_version(Experiment.DEFAULT_VERSION_NUMBER) == second_version
        assert first_version.get_version(Experiment.DEFAULT_VERSION_NUMBER) == second_version

        # Get the working version. Its version number should be 3
        assert experiment.get_version(3) == experiment
        assert first_version.get_version(3) == experiment

        # Get a specific version
        assert experiment.get_version(1) == first_version
        assert first_version.get_version(1) == first_version

    def test_archive_working_experiment(self, experiment):
        """Test that archiving an experiment archives all versions and deletes its scheduled messages"""
        session = ExperimentSessionFactory(experiment=experiment)
        event_action = EventActionFactory(
            action_type=EventActionType.SCHEDULETRIGGER,
            params={
                "name": "Test",
                "time_period": TimePeriod.DAYS,
                "frequency": 1,
                "repetitions": 1,
                "prompt_text": "hi",
                "experiment_id": experiment.id,
            },
        )
        ScheduledMessageFactory(
            experiment=experiment, team=experiment.team, participant=session.participant, action=event_action
        )
        assert ScheduledMessage.objects.filter(experiment=experiment).exists() is True

        first_version = experiment.create_new_version()
        second_version = experiment.create_new_version()
        experiment.archive()
        experiment.refresh_from_db()
        first_version.refresh_from_db()
        second_version.refresh_from_db()

        assert experiment.is_archived is True
        assert first_version.is_archived is True
        assert second_version.is_archived is True
        assert ScheduledMessage.objects.filter(experiment=experiment).exists() is False

    @patch("apps.assistants.tasks.delete_openai_assistant_task.delay")
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_with_pipeline(self, delete_openai_assistant_task):
        assistant = OpenAiAssistantFactory()
        pipeline = PipelineFactory()
        NodeFactory(pipeline=pipeline, type="AssistantNode", params={"assistant_id": assistant.id})
        experiment = ExperimentFactory(pipeline=pipeline)

        # For a version, the pipeline should be archived as well as the assistant that it references
        new_version = experiment.create_new_version()
        assert assistant.versions.count() == 1
        assistant_version = assistant.versions.first()
        new_version.archive()
        self._assert_archived(new_version.pipeline, True)
        self._assert_archived(assistant_version, True)
        delete_openai_assistant_task.assert_called_with(assistant_version.id)
        delete_openai_assistant_task.reset_mock()

        experiment.archive()
        self._assert_archived(experiment.pipeline, False)
        self._assert_archived(assistant, False)

    def _assert_archived(self, model_obj, archived: bool):
        model_obj.refresh_from_db(fields=["is_archived"])
        assert model_obj.is_archived == archived


@pytest.mark.django_db()
class TestExperimentObjectManager:
    def test_get_default_or_working(self):
        working_exp = ExperimentFactory(version_number=3)
        # With no versions, working_exp should be returned
        assert Experiment.objects.get_default_or_working(family_member=working_exp) == working_exp

        # With versions, the default version should be returned
        team = working_exp.team
        exp_v1 = ExperimentFactory(team=team, version_number=2, working_version=working_exp)
        exp_v2 = ExperimentFactory(team=team, version_number=3, working_version=working_exp, is_default_version=True)

        assert Experiment.objects.get_default_or_working(family_member=working_exp) == exp_v2
        assert Experiment.objects.get_default_or_working(family_member=exp_v1) == exp_v2
        assert Experiment.objects.get_default_or_working(family_member=exp_v2) == exp_v2

    def test_working_versions_queryset(self):
        experiments = ExperimentFactory.create_batch(3)
        for working_exp in experiments:
            working_exp.create_new_version()

        working_versions_queryset = Experiment.objects.working_versions_queryset()
        assert working_versions_queryset.count() == 3
        for working_version in working_versions_queryset.all():
            # All experiments in this queryset should have versions
            assert working_version.has_versions is True

    def test_archived_experiments_are_filtered_out(self):
        """Default queries should exclude archived experiments"""
        experiment = ExperimentFactory()
        new_version = experiment.create_new_version()
        assert Experiment.objects.count() == 2
        new_version.is_archived = True
        new_version.save()
        assert Experiment.objects.count() == 1

        # To get all experiment,s use the dedicated object method
        assert Experiment.objects.get_all().count() == 2


@pytest.mark.django_db()
class TestExperimentTrends:
    def test_get_trend_data_returns_data_from_all_versions(self, experiment):
        """Test that get_trend_data aggregates traces from all versions of an experiment"""
        # Create some versions of the experiment
        version1 = experiment.create_new_version()
        version2 = experiment.create_new_version()

        curr_time = timezone.now()
        Trace.objects.create(
            experiment=experiment, team=experiment.team, status=TraceStatus.SUCCESS, timestamp=curr_time, duration=1
        )
        # Trace for version1
        Trace.objects.create(
            experiment=version1, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
        )
        # Trace for version2
        Trace.objects.create(
            experiment=version2, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
        )

        success, errors = experiment.get_trend_data()

        # Should aggregate traces from all versions: 1 success + 2 errors
        assert sum(success) == 1, f"Expected 1 success, got {sum(success)}"
        assert sum(errors) == 2, f"Expected 2 errors, got {sum(errors)}"

    def test_get_experiment_trend_data_with_no_errors(self, experiment):
        """Test that the function returns an array of zeros when there are no error traces"""
        success, errors = experiment.get_trend_data()
        empty_data = [0] * 49
        assert errors == empty_data
        assert success == empty_data

    def test_get_experiment_trend_data_with_errors(self, experiment):
        """Test that the function returns error counts when there are error traces"""
        # Create traces with error status
        with time_machine.travel("2025-01-01 12:00:00") as curr_time:
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.SUCCESS, timestamp=curr_time, duration=1
            )
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 10:00:00"):
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 7:00:00"):
            Trace.objects.create(
                experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, timestamp=curr_time, duration=1
            )

        with time_machine.travel("2025-01-01 13:00:00") as curr_time:
            success, errors = experiment.get_trend_data()

        # Should return actual error counts (2 errors in one hour, 1 in another)
        assert isinstance(errors, list)
        assert sum(errors) == 3
        assert sum(success) == 1

    def test_get_experiment_trend_data_only_recent_errors(self, experiment):
        """Test that only errors within the last 2 days are counted"""
        # Mock current time
        with time_machine.travel("2025-08-15 12:00:00"):
            # Create an error trace outside the 2-day window
            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

        # Create an error trace within the 2-day window
        with time_machine.travel("2025-08-21 12:00:00"):
            # Create an error trace outside the 2-day window
            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

            Trace.objects.create(experiment=experiment, team=experiment.team, status=TraceStatus.ERROR, duration=1)

            success, error = experiment.get_trend_data()

            # Should only count the recent error
            assert sum(error) == 2
            assert experiment.traces.filter(status=TraceStatus.ERROR).count() == 3
            assert sum(success) == 0


def _compare_models(original, new, expected_changed_fields: list) -> set:
    field_difference = original.compare_with_model(new, original.get_fields_to_exclude()).difference(
        set(expected_changed_fields)
    )
    assert field_difference == set(), (
        f"These fields differ between the experiment versions, but should not: {field_difference}"
    )
