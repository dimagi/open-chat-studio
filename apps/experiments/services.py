from django.db import transaction

from .models import Experiment, ExperimentVersion
from .serializers import populate_experiment_version_data


@transaction.atomic()
def save_experiment_version(experiment: Experiment, is_default: bool = False) -> str:
    version_data = populate_experiment_version_data(experiment)
    if version_data:
        last_version_number = (
            ExperimentVersion.objects.filter(experiment=experiment)
            .order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
        )
        next_version_number = last_version_number + 1 if last_version_number else 1

        ExperimentVersion.objects.create(
            experiment=experiment,
            version_data=version_data.model_dump(exclude_unset=True, exclude_none=True),
            is_default=is_default,
            version_number=next_version_number,
            team=experiment.team,
        )
        return "Success"
    return "Failed to save version"


def switch_default_experiment_version(experiment: Experiment, version_id: int) -> str:
    try:
        with transaction.atomic():
            previous_default = ExperimentVersion.objects.filter(experiment=experiment, is_default=True)
            version_to_update = ExperimentVersion.objects.filter(id=version_id, experiment=experiment)
            if not previous_default or not version_to_update:
                return "No version found."
            previous_default.update(is_default=False)
            update_count = version_to_update.update(is_default=True)
            print("update_count")
            print(update_count)
            if update_count != 1:
                previous_default.update(is_default=True)
                return "Failed to switch default version. No version was updated."
        return "Default version switched successfully"
    except Exception as e:
        previous_default.update(is_default=True)
        return f"Error switching default version: {e}"
