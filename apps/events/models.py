from datetime import datetime, timedelta

from django.db import models
from pytz import UTC

from apps.chat.models import ChatMessageType
from apps.experiments.models import Experiment
from apps.utils.models import BaseModel


class TimeoutTrigger(BaseModel):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="timeout_triggers")
    delay = models.PositiveIntegerField(
        help_text="The amount of time in seconds to fire this trigger.",
    )
    total_num_triggers = models.IntegerField(
        default=1,
        help_text="The number of times to fire this trigger",
    )
    end_conversation = models.BooleanField(
        default=True,
        help_text="Whether to end the conversation after all the triggers have expired",
    )
    last_triggered = models.DateTimeField(null=True, help_text="The last time this trigger was fired")
    trigger_count = models.IntegerField(
        default=0,
        help_text="The number of times this trigger was fired",
    )

    def timed_out_sessions(self):
        sessions = []
        trigger_time = datetime.now().astimezone(UTC) - timedelta(seconds=self.delay)
        open_experiment_sessions = self.experiment.sessions.filter(ended_at=None)
        for session in open_experiment_sessions:
            last_human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).last()
            if last_human_message and last_human_message.created_at < trigger_time:
                # TODO: should this use updated_at instead? When can you edit a chat message?
                sessions.append(session)
        return sessions


# class EventActionType(Enum):
#     SUMMARIZE = "summarize"     # requires prompt


# class EventAction(BaseModel):
#     experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="event_actions")
#     action_type = models.CharField(choices=EventActionType)
#     params = models.JSONField()  # The parameters for the specific action


# class EventTrigger(BaseTeamModel):
#     experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="event_triggers")
#     action = models.ForeignKey(EventAction, on_delete=models.CASCADE, related_name="triggers")
