from django.db import transaction

from apps.analysis.core import Pipeline
from apps.events.models import EventAction

from .models import Experiment


def create_experiment_version(original_experiment_id):
    """
    Creates a copy of an experiment as a new version of the original experiment.
    """
    original_experiment = Experiment.objects.get(id=original_experiment_id)
    with transaction.atomic():
        new_experiment = original_experiment
        new_experiment.pk = None
        new_experiment.working_experiment = original_experiment
        new_experiment.status = "Released"
        new_experiment.is_active = False
        new_experiment.version_number = calculate_version_number(original_experiment_id)
        new_experiment.save()
        new_experiment.safety_layers.set(original_experiment.safety_layers.all())
        new_experiment.files.set(original_experiment.files.all())
        copy_event_actions(original_experiment_id)
    return new_experiment


def calculate_version_number(parent_experiment_id):
    last_version_number = (
        Experiment.objects.filter(parent_id=parent_experiment_id)
        .order_by("-version_number")
        .values_list("version_number", flat=True)
        .first()
        or 0
    )
    return last_version_number + 1


def copy_event_actions(experiment_id):
    event_actions = EventAction.objects.filter(experiment_id=experiment_id)
    for event_action in event_actions:
        event_action.pk = None
        if "pipeline_id" in event_action.params:
            old_pipeline_id = event_action.params["pipeline_id"]
            old_pipeline = Pipeline.objects.get(id=old_pipeline_id)
            new_pipeline = Pipeline.objects.create(name=f"{old_pipeline.name} (copy)", data=old_pipeline.data)
            event_action.params["pipeline_id"] = new_pipeline.id
        event_action.save()
