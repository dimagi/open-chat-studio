from unittest.mock import Mock, patch

from apps.chat.bots import TopicBot
from apps.experiments.models import SafetyLayer
from apps.utils.factories.experiment import ExperimentSessionFactory


@patch("apps.chat.bots.notify_users_of_violation")
@patch("apps.chat.bots.create_conversation")
@patch("apps.chat.bots.SafetyBot.is_safe", lambda *args: False)
def test_safety_layer_violation(notify_users_of_violation_mock, create_conversation_mock, db):
    """
    The following is expected to happen when a safety layer is breached:
    1. Notification emails should be sent to configured emails
    2. The user message that breached that should still be saved
    """
    experiment_session = ExperimentSessionFactory(experiment__safety_violation_notification_emails=["user@officer.com"])
    experiment = experiment_session.experiment
    layer = SafetyLayer.objects.create(prompt_text="Is this message safe?", team=experiment.team)
    experiment.safety_layers.add(layer)

    bot = TopicBot(experiment_session)
    bot.conversation = Mock()
    bot._call_predict = Mock()
    user_message = "It's my way or the highway!"
    bot.process_input(user_message)
    notify_users_of_violation_mock.assert_called()
    assert experiment_session.chat.messages.filter(message_type="human", content=user_message).count() == 1
