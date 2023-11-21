import pytest
from django.test import TestCase

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import ConsentForm, Experiment, Prompt
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team
from apps.users.models import CustomUser


class TestExperimentChannelObjectManager(TestCase):
    def setUp(self):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.team = Team.objects.create(name="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.prompt = Prompt.objects.create(
            team=self.team,
            owner=self.user,
            name="test-prompt",
            description="test",
            prompt="You are a helpful assistant",
        )
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            chatbot_prompt=self.prompt,
            consent_form=ConsentForm.get_default(self.team),
        )
        self.bot_token = "123123123"
        self.bot_token_key = "bot_token"
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel",
            experiment=self.experiment,
            extra_data={self.bot_token_key: self.bot_token},
            platform=ChannelPlatform.TELEGRAM,
        )

    def test_filter_extras_successs(self):
        test_cases = [
            (self.bot_token_key, self.bot_token, ChannelPlatform.TELEGRAM, self.team.slug, 1),
            (self.bot_token_key, self.bot_token, ChannelPlatform.FACEBOOK, self.team.slug, 0),
            (self.bot_token_key, "123", ChannelPlatform.TELEGRAM, self.team.slug, 0),
            ("bot_tokens", self.bot_token, ChannelPlatform.TELEGRAM, self.team.slug, 0),
            (self.bot_token_key, self.bot_token, ChannelPlatform.TELEGRAM, "some-other-team", 0),
        ]
        for test_case in test_cases:
            key, value, platform, team_slug, expected_result_length = test_case
            channels = ExperimentChannel.objects.filter_extras(
                key=key, value=value, platform=platform, team_slug=team_slug
            )
            self.assertEqual(len(channels), expected_result_length)
