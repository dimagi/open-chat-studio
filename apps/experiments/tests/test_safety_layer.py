from unittest.mock import Mock, patch

from apps.chat.bots import TopicBot
from apps.experiments.models import SafetyLayer
from apps.utils.factories.experiment import ExperimentSessionFactory


@patch("apps.chat.bots.notify_users_of_violation")
@patch("apps.chat.bots.create_conversation")
@patch("apps.chat.bots.SafetyBot.is_safe")
def test_violation_triggers_email(notify_users_of_violation_mock, create_conversation_mock, is_safe_mock, db):
    is_safe_mock.return_value = False
    experiment_session = ExperimentSessionFactory(experiment__safety_violation_notification_emails=["user@officer.com"])
    experiment = experiment_session.experiment
    layer = SafetyLayer.objects.create(prompt_text="Is this message safe?", team=experiment.team)
    experiment.safety_layers.add(layer)

    bot = TopicBot(experiment_session)
    bot.conversation = Mock()
    bot._call_predict = Mock()
    bot.process_input("It's my way or the highway!")
    notify_users_of_violation_mock.assert_called()
