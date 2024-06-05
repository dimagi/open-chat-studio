from unittest.mock import Mock, patch

import pytest

from apps.experiments.models import SyntheticVoice
from apps.utils.factories.experiment import ExperimentSessionFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


class TestExperimentSession:
    @pytest.mark.django_db()
    @pytest.mark.parametrize("fail_silently", [True, False])
    @patch("apps.chat.channels.ChannelBase.from_experiment_session")
    @patch("apps.chat.bots.TopicBot.process_input")
    def test_ad_hoc_message(self, process_input, from_experiment_session, fail_silently, experiment_session):
        mock_channel = Mock()
        mock_channel.new_bot_message = Mock()
        if not fail_silently:
            mock_channel.new_bot_message.side_effect = Exception("Cannot send message")
        from_experiment_session.return_value = mock_channel
        process_input.return_value = "We're testing"

        def _test():
            experiment_session.ad_hoc_bot_message(
                instruction_prompt="Tell the user we're testing", fail_silently=fail_silently
            )
            call = mock_channel.new_bot_message.mock_calls[0]
            assert call.args[0] == "We're testing"

        if not fail_silently:
            with pytest.raises(Exception, match="Cannot send message"):
                _test()
        else:
            _test()


class TestSyntheticVoice:
    @pytest.mark.django_db()
    def test_team_scoped_services(self):
        assert SyntheticVoice.TEAM_SCOPED_SERVICES == [SyntheticVoice.OpenAIVoiceEngine]

    @pytest.mark.django_db()
    def test_get_for_team_returns_all_general_services(self):
        """General services are those not included in SyntheticVoice.TEAM_SCOPED_SERVICES"""
        voices_queryset = SyntheticVoice.get_for_team(team=None)
        assert voices_queryset.count() == SyntheticVoice.objects.count()

    @pytest.mark.django_db()
    def test_get_for_team_excludes_service(self):
        voices_queryset = SyntheticVoice.get_for_team(team=None, exclude_services=[SyntheticVoice.AWS])
        services = set(voices_queryset.values_list("service", flat=True))
        assert services == {SyntheticVoice.OpenAI, SyntheticVoice.Azure}

    @pytest.mark.django_db()
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
