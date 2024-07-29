from django.db import transaction
from experiments.models import Experiment, ExperimentVersion
from experiments.serializers import populate_experiment_version_data


def save_experiment_version(experiment: Experiment, is_default: bool = False) -> str:
    version_data = populate_experiment_version_data(experiment)
    if version_data:
        last_version = ExperimentVersion.objects.filter(experiment=experiment).order_by("-version_number").first()
        next_version_number = last_version.version_number + 1 if last_version else 1

        ExperimentVersion.objects.create(
            experiment=experiment,
            version_data=version_data.json(),
            is_default=is_default,
            version_number=next_version_number,
        )
        return "Success"
    return "Failed to save version"


def switch_default_experiment_version(experiment: Experiment, version_id: int) -> str:
    try:
        with transaction.atomic():
            ExperimentVersion.objects.filter(experiment=experiment, is_default=True).update(is_default=False)
            version = ExperimentVersion.objects.get(id=version_id, experiment=experiment)
            version.is_default = True
            version.save()
        return "Default version switched successfully"
    except ExperimentVersion.DoesNotExist:
        return "Version not found"
    except Exception as e:
        return f"Error switching default version: {e}"
