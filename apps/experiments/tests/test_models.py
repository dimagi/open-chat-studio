from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

from apps.events.actions import ScheduleTriggerAction
from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.experiments.models import ExperimentRoute, ParticipantData, SyntheticVoice
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
    SyntheticVoiceFactory,
)
from apps.utils.factories.service_provider_factories import VoiceProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


class TestSyntheticVoice:
    @django_db_with_data()
    def test_team_scoped_services(self):
        assert SyntheticVoice.TEAM_SCOPED_SERVICES == [SyntheticVoice.OpenAIVoiceEngine]

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


class TestExperimentSession:
    def _construct_event_action(self, time_period: TimePeriod, experiment_id: int, frequency=1, repetitions=1) -> tuple:
        params = {
            "name": "Test",
            "time_period": time_period,
            "frequency": frequency,
            "repetitions": repetitions,
            "prompt_text": "",
            "experiment_id": experiment_id,
        }
        return EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER), params

    @pytest.mark.django_db()
    @freeze_time("2024-01-01")
    def test_get_participant_scheduled_messages(self):
        session = ExperimentSessionFactory()
        event_action = event_action, params = self._construct_event_action(
            time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
        )
        ScheduledMessageFactory.create_batch(
            size=2,
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=event_action,
        )
        assert len(session.get_participant_scheduled_messages()) == 2
        expected_str_version = [
            "Test: Every 1 days on Tuesday for 1 times (next trigger is Tuesday, 02 January 2024 00:00:00 UTC)",
            "Test: Every 1 days on Tuesday for 1 times (next trigger is Tuesday, 02 January 2024 00:00:00 UTC)",
        ]
        expected_dict_version = [
            {
                "name": "Test",
                "frequency": 1,
                "time_period": "days",
                "repetitions": 1,
                "next_trigger_date": "2024-01-02T00:00:00+00:00",
            },
            {
                "name": "Test",
                "frequency": 1,
                "time_period": "days",
                "repetitions": 1,
                "next_trigger_date": "2024-01-02T00:00:00+00:00",
            },
        ]
        assert session.get_participant_scheduled_messages() == expected_str_version
        assert session.get_participant_scheduled_messages(as_dict=True) == expected_dict_version

    @pytest.mark.django_db()
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

        assert len(session2.get_participant_scheduled_messages()) == 1
        assert len(session.get_participant_scheduled_messages()) == 2

    @pytest.mark.django_db()
    @pytest.mark.parametrize("use_custom_experiment", [False, True])
    def test_scheduled_message_experiment(self, use_custom_experiment):
        """ScheduledMessages should use the experiment specified in the linked action's params"""
        custom_experiment = ExperimentFactory() if use_custom_experiment else None
        session = ExperimentSessionFactory()
        event_action_kwargs = {"time_period": TimePeriod.DAYS, "experiment_id": session.experiment.id}
        if custom_experiment:
            event_action_kwargs["experiment_id"] = custom_experiment.id

        event_action, params = self._construct_event_action(**event_action_kwargs)
        trigger_action = ScheduleTriggerAction()
        trigger_action.invoke(session, action=event_action)

        session.ad_hoc_bot_message = Mock()
        message = ScheduledMessage.objects.get(action=event_action)
        message.participant.get_latest_session = lambda *args, **kwargs: session
        message.safe_trigger()

        experiment_used = session.ad_hoc_bot_message.call_args_list[0].kwargs["use_experiment"]
        if use_custom_experiment:
            assert experiment_used == custom_experiment
        else:
            assert experiment_used == session.experiment

    @pytest.mark.django_db()
    @freeze_time("2022-01-01 08:00:00")
    @pytest.mark.parametrize("use_participant_tz", [False, True])
    def test_get_participant_data_timezone(self, use_participant_tz):
        participant = ParticipantFactory()
        session = ExperimentSessionFactory(participant=participant, team=participant.team)
        event_action = event_action, params = self._construct_event_action(
            time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
        )
        ScheduledMessageFactory(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=event_action,
        )
        ParticipantData.objects.create(
            content_object=session.experiment,
            participant=participant,
            team=participant.team,
            data={"name": "Tester", "timezone": "Africa/Johannesburg"},
        )
        timezone_time = "10:00:00 SAST" if use_participant_tz else "08:00:00 UTC"
        expected_data = {
            "name": "Tester",
            "timezone": "Africa/Johannesburg",
            "scheduled_messages": [
                f"Test: Every 1 days on Sunday for 1 times (next trigger is Sunday, 02 January 2022 {timezone_time})"
            ],
        }
        assert session.get_participant_data(use_participant_tz=use_participant_tz) == expected_data

    @pytest.mark.django_db()
    @pytest.mark.parametrize("fail_silently", [True, False])
    @patch("apps.chat.channels.ChannelBase.from_experiment_session")
    @patch("apps.chat.bots.TopicBot.process_input")
    def test_ad_hoc_message(self, process_input, from_experiment_session, fail_silently, experiment_session):
        mock_channel = Mock()
        mock_channel.send_message_to_user = Mock()
        if not fail_silently:
            mock_channel.send_message_to_user.side_effect = Exception("Cannot send message")
        from_experiment_session.return_value = mock_channel
        process_input.return_value = "We're testing"

        def _test():
            experiment_session.ad_hoc_bot_message(
                instruction_prompt="Tell the user we're testing", fail_silently=fail_silently
            )
            call = mock_channel.send_message_to_user.mock_calls[0]
            assert call.args[0] == "We're testing"

        if not fail_silently:
            with pytest.raises(Exception, match="Cannot send message"):
                _test()
        else:
            _test()


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
            content_object=sessions[0].experiment,
            data={"first_name": "Jack", "last_name": "Turner"},
            participant=participant,
        )
        participant.update_memory({"first_name": "Elizabeth"})
        participant_data_query = ParticipantData.objects.filter(team=team, participant=participant)
        assert participant_data_query.count() == 3
        for p_data in participant_data_query.all():
            if p_data == existing_data_obj:
                assert p_data.data == {"first_name": "Elizabeth", "last_name": "Turner"}
            else:
                assert p_data.data == {"first_name": "Elizabeth"}

    @pytest.mark.django_db()
    def test_update_memory_for_specific_experiment(self):
        participant = ParticipantFactory()
        team = participant.team
        sessions = ExperimentSessionFactory.create_batch(3, participant=participant, team=team, experiment__team=team)
        experiment = sessions[0].experiment
        participant.update_memory({"first_name": "Will"}, experiment=experiment)
        assert ParticipantData.objects.count() == 1
        assert experiment.participant_data.filter(participant=participant).first().data == {"first_name": "Will"}
        participant.update_memory({"first_name": "Bootstrap Bill", "last_name": "Turner"}, experiment=experiment)
        assert experiment.participant_data.filter(participant=participant).first().data == {
            "first_name": "Bootstrap Bill",
            "last_name": "Turner",
        }
