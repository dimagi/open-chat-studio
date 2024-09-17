from apps.events.models import EventActionType, TimePeriod
from apps.utils.factories.events import EventActionFactory


def construct_event_action(
    time_period: TimePeriod, frequency=1, repetitions=1, experiment_id: int | None = None
) -> tuple:
    params = {
        "name": "Test",
        "time_period": time_period,
        "frequency": frequency,
        "repetitions": repetitions,
        "prompt_text": "",
        "experiment_id": experiment_id,
    }
    return EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER), params
