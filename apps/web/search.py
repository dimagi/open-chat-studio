import dataclasses

from django.db.models import Model


@dataclasses.dataclass
class SearchableModel:
    model_cls: type[Model]
    field_name: str
    require_uuid: bool = True

    def search(self, value):
        results = self.model_cls.objects.filter(**{self.field_name: value})[:2]
        if len(results) == 1:
            return results[0]

    @property
    def permission(self):
        app_label = self.model_cls._meta.app_label
        model_name = self.model_cls._meta.model_name
        return f"{app_label}.view_{model_name}"


def get_searchable_models(model_name: None):
    from apps.chat.models import ChatMessage
    from apps.experiments.models import Experiment, ExperimentSession, Participant

    searchable_models = [
        SearchableModel(Experiment, "public_id"),
        SearchableModel(ExperimentSession, "external_id"),
        SearchableModel(Participant, "public_id"),
        SearchableModel(ChatMessage, "id", False),
    ]

    if model_name:
        return [item for item in searchable_models if item.model_cls.__name__ == model_name]

    return searchable_models
