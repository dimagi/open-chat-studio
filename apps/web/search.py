import dataclasses
import re
from urllib.parse import urlencode

from django.db.models import Model
from django.urls import reverse

from apps.web.meta import absolute_url


@dataclasses.dataclass
class SearchableModel:
    model_cls: type[Model]
    field_name: str
    regex: re.Pattern = re.compile(r"^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$", re.IGNORECASE)

    def search(self, value):
        if not self.regex.match(value):
            return None
        results = self.model_cls.objects.filter(**{self.field_name: value})[:2]
        if len(results) == 1:
            return results[0]

    @property
    def permission(self):
        app_label = self.model_cls._meta.app_label
        model_name = self.model_cls._meta.model_name
        return f"{app_label}.view_{model_name}"


def get_searchable_models(model_name: str | None):
    from apps.chat.models import ChatMessage
    from apps.experiments.models import Experiment, ExperimentSession, Participant

    searchable_models = [
        SearchableModel(Experiment, "public_id"),
        SearchableModel(ExperimentSession, "external_id"),
        SearchableModel(Participant, "public_id"),
        SearchableModel(ChatMessage, "id", re.compile(r"^\d+$")),
    ]

    if model_name:
        return [item for item in searchable_models if item.model_cls.__name__ == model_name]

    return searchable_models


def get_global_search_url(instance: Model) -> str:
    """Generate a global search URL for a model instance."""
    model_name = instance.__class__.__name__
    searchable_models = get_searchable_models(model_name)

    if not searchable_models:
        return ""

    searchable_model = searchable_models[0]
    field_value = getattr(instance, searchable_model.field_name)

    uri = reverse("web:global_search")
    query_string = urlencode({"q": field_value, "m": model_name})
    return absolute_url(uri) + f"?{query_string}"
