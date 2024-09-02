from unittest.mock import Mock, patch

import pytest
from django.db.utils import IntegrityError
from freezegun import freeze_time

from apps.events.actions import ScheduleTriggerAction
from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.experiments.models import ExperimentRoute, ParticipantData, SafetyLayer, SyntheticVoice
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
    SourceMaterialFactory,
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
        event_action, params = self._construct_event_action(
            time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
        )
        message1 = ScheduledMessageFactory(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            action=event_action,
        )
        message2 = ScheduledMessageFactory(
            experiment=session.experiment,
            team=session.team,
            participant=session.participant,
            custom_schedule_params=params,
            action=None,
        )
        assert len(session.get_participant_scheduled_messages()) == 2
        str_version1 = (
            f"{message1.name} (ID={message1.external_id}, message={message1.prompt_text}): Every 1 days on Tuesday, "
            "1 times. Next trigger is at Tuesday, 02 January 2024 00:00:00 UTC. (System)"
        )
        str_version2 = (
            f"{message2.name} (ID={message2.external_id}, message={message2.prompt_text}): Every 1 days on Tuesday, "
            "1 times. Next trigger is at Tuesday, 02 January 2024 00:00:00 UTC. "
        )

        scheduled_messages_str = session.get_participant_scheduled_messages()
        assert str_version1 in scheduled_messages_str
        assert str_version2 in scheduled_messages_str

        expected_dict_version = [
            {
                "name": "Test",
                "external_id": message1.external_id,
                "frequency": 1,
                "time_period": "days",
                "repetitions": 1,
                "next_trigger_date": "2024-01-02T00:00:00+00:00",
            },
            {
                "name": "Test",
                "external_id": message2.external_id,
                "frequency": 1,
                "time_period": "days",
                "repetitions": 1,
                "next_trigger_date": "2024-01-02T00:00:00+00:00",
            },
        ]
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
        expected_data = {
            "name": "Tester",
            "timezone": "Africa/Johannesburg",
        }
        participant_data = session.get_participant_data(use_participant_tz=use_participant_tz)
        # test_get_participant_scheduled_messages is testing the schedule format, so pop it so we don't have to update
        # this test as well when we update the string representation of the schedule
        participant_data.pop("scheduled_messages")
        assert participant_data == expected_data

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
    def test_create_new_version(self):
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
    def test_create_new_version(self):
        original = SourceMaterialFactory()
        new_version = original.create_new_version()
        original.refresh_from_db()
        assert original.working_version is None
        assert new_version != original
        assert new_version.description == original.description
        assert new_version.material == original.material
        assert new_version.team == original.team


@pytest.mark.django_db()
class TestExperimentRouteVersioning:
    def test_create_new_route_version(self):
        original_parent = ExperimentFactory()
        team = original_parent.team
        new_parent = ExperimentFactory(team=team)
        original = ExperimentRoute.objects.create(
            team=team, parent=ExperimentFactory(), child=ExperimentFactory(team=team), keyword="testing"
        )

        new_version = original.create_new_version(new_parent)
        assert new_version != original


@pytest.mark.django_db()
class TestExperimentVersioning:
    def test_working_experiment_cannot_be_the_default_version(self):
        with pytest.raises(ValueError, match="A working experiment cannot be a default version"):
            ExperimentFactory(is_default_version=True, working_version=None)

    def test_single_default_version_per_experiment(self):
        working_exp = ExperimentFactory()
        team = working_exp.team
        ExperimentFactory(is_default_version=True, working_version=working_exp, team=team)
        with pytest.raises(IntegrityError, match=r'.*"unique_default_version_per_experiment".*'):
            ExperimentFactory(is_default_version=True, working_version=working_exp, team=team, version_number=2)
        ExperimentFactory(is_default_version=False, working_version=working_exp, team=team, version_number=3)

    def test_unique_version_number_per_experiment(self):
        working_exp = ExperimentFactory()
        team = working_exp.team
        ExperimentFactory(working_version=working_exp, team=team, version_number=2)
        with pytest.raises(IntegrityError, match=r'.*"unique_version_number_per_experiment".*'):
            ExperimentFactory(working_version=working_exp, team=team, version_number=2)

    def _setup_original_experiment(self):
        experiment = ExperimentFactory()
        team = experiment.team

        # Safety Layers
        layer1 = SafetyLayer.objects.create(
            prompt_text="Is this message safe?", team=team, prompt_to_bot="Unsafe reply"
        )
        layer2 = SafetyLayer.objects.create(prompt_text="What about this one?", team=team, prompt_to_bot="Unsafe reply")
        experiment.safety_layers.set([layer1, layer2])

        # Source material
        experiment.source_material = SourceMaterialFactory(team=team, material="material science is interesting")
        experiment.save()

        # Routes - There will be versioned and working children
        versioned_child = ExperimentFactory(
            team=team, version_number=1, working_version=ExperimentFactory(version_number=2)
        )
        ExperimentRoute(team=team, parent=experiment, child=versioned_child, keyword="versioned")
        working_child = ExperimentFactory(team=team)
        ExperimentRoute(team=team, parent=experiment, child=working_child, keyword="working")
        return experiment

    @pytest.mark.django_db()
    def original_experiment(self):
        original_experiment = self._setup_original_experiment()

        assert original_experiment.version_number == 1

        new_version = original_experiment.create_new_version()
        assert new_version != original_experiment
        assert original_experiment.version_number == 2
        assert original_experiment.working_version is None
        assert new_version.version_number == 1
        assert new_version.working_version == original_experiment
        self._assert_duplicated_safety_layers(original_experiment, new_version)
        self._assert_duplicated_source_material(original_experiment, new_version)

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
            assert route.child.is_versioned is True
