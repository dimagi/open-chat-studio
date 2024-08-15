from django.db import transaction

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
