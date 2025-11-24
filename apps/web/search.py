import dataclasses

from django.db.models import Model


@dataclasses.dataclass
class SearchableModel:
    model_cls: type[Model]
    field_name: str

    def search(self, value):
        results = self.model_cls.objects.filter(**{self.field_name: value})[:2]
        if len(results) == 1:
            return results[0]

    @property
    def permission(self):
        app_label = self.model_cls._meta.app_label
        model_name = self.model_cls._meta.model_name
        return f"{app_label}.view_{model_name}"


def get_searchable_models():
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.participants.models import Participant

    return [
        SearchableModel(Experiment, "public_id"),
        SearchableModel(ExperimentSession, "external_id"),
        SearchableModel(Participant, "public_id"),
    ]
