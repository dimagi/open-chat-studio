import pytest

from apps.chat.models import ScheduledMessageConfig, TimePeriod, TriggerEvent


def test_validation_error_raised(experiment):
    data = {
        "name": "pesky reminder",
        "team": experiment.team,
        "experiment": experiment,
        "trigger_event": TriggerEvent.CONVERSATION_START,
        "recurring": True,
        "time_period": TimePeriod.WEEKS,
        "frequency": 2,
        "reptitions": 0,
        "prompt_text": "Check in with the user",
    }
    with pytest.raises(ValueError, match="Recurring schedules require `reptitions` to be larger than 0"):
        ScheduledMessageConfig.objects.create(**data)

    data["recurring"] = False
    data["reptitions"] = 2
    with pytest.raises(ValueError, match="Non recurring schedules cannot have `reptitions` larger than 0"):
        ScheduledMessageConfig.objects.create(**data)
