from uuid import uuid4

from django.db import transaction

from .models import Experiment


def create_experiment_version(working_experiment: Experiment) -> Experiment:
    """
    Creates a copy of an experiment as a new version of the original experiment.
    """
    working_experiment.refresh_from_db()
    with transaction.atomic():
        version_number = working_experiment.version_number
        working_experiment.version_number = version_number + 1
        working_experiment.save()
        working_experiment_id = working_experiment.id

        # Fetch a new instance so the previous instance reference isn't simply being updated. I am not 100% sure
        # why simply chaing the pk, id and _state.adding wasn't enough.
        new_experiment = Experiment.objects.get(id=working_experiment_id)
        new_experiment._state.adding = True
        new_experiment.pk = None
        new_experiment.id = None
        new_experiment.public_id = uuid4()
        new_experiment.working_experiment_id = working_experiment_id
        new_experiment.version_number = version_number
        new_experiment.save()
        # new_experiment.safety_layers.set(original_experiment.safety_layers.all()) # TODO
        # new_experiment.files.set(original_experiment.files.all()) # TODO
    return new_experiment
