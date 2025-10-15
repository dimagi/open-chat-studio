import inspect

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.evaluations.forms import EvaluatorForm
from apps.evaluations.models import Evaluator
from apps.evaluations.tables import EvaluatorTable
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.mixins import LoginAndTeamRequiredMixin


class EvaluatorHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluator"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluators",
            "title": "Evaluators",
            "new_object_url": reverse("evaluations:evaluator_new", args=[team_slug]),
            "table_url": reverse("evaluations:evaluator_table", args=[team_slug]),
        }


class EvaluatorTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluator"
    model = Evaluator
    paginate_by = 25
    table_class = EvaluatorTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            Evaluator.objects.filter(team=self.request.team)
            # .annotate(run_count=Count("runs"))
            # .order_by("name")
        )


class CreateEvaluator(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "evaluations.add_evaluator"
    template_name = "evaluations/evaluator_form.html"
    model = Evaluator
    form_class = EvaluatorForm
    extra_context = {
        "title": "Create Evaluator",
        "button_text": "Create",
        "active_tab": "evaluators",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        llm_providers = LlmProvider.objects.filter(team=self.request.team).values("id", "name", "type").all()
        llm_provider_models = LlmProviderModel.objects.for_team(self.request.team).all()
        context.update(
            {
                "evaluator_schemas": _evaluator_schemas(),
                "parameter_values": _evaluator_parameter_values(self.request.team, llm_providers, llm_provider_models),
                "default_values": _evaluator_default_values(llm_providers, llm_provider_models),
            }
        )
        return context

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:evaluator_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditEvaluator(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "evaluations.change_evaluator"
    model = Evaluator
    form_class = EvaluatorForm
    template_name = "evaluations/evaluator_form.html"
    extra_context = {
        "title": "Update Evaluator",
        "button_text": "Update",
        "active_tab": "evaluators",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        llm_providers = LlmProvider.objects.filter(team=self.request.team).values("id", "name", "type").all()
        llm_provider_models = LlmProviderModel.objects.for_team(self.request.team).all()
        context.update(
            {
                "evaluator_schemas": _evaluator_schemas(),
                "parameter_values": _evaluator_parameter_values(self.request.team, llm_providers, llm_provider_models),
                "default_values": _evaluator_default_values(llm_providers, llm_provider_models),
            }
        )
        return context

    def get_queryset(self):
        return Evaluator.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:evaluator_home", args=[self.request.team.slug])


class DeleteEvaluator(LoginAndTeamRequiredMixin, DeleteView, PermissionRequiredMixin):
    permission_required = "evaluations.delete_evaluator"
    model = Evaluator

    def get_queryset(self):
        return Evaluator.objects.filter(team=self.request.team)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return HttpResponse(status=200)


def _evaluator_schemas():
    """Returns schemas for all available evaluator classes."""
    from apps.evaluations import evaluators

    schemas = []

    evaluator_classes = [
        cls
        for _, cls in inspect.getmembers(evaluators, inspect.isclass)
        if issubclass(cls, evaluators.BaseEvaluator) and cls != evaluators.BaseEvaluator
    ]

    for evaluator_class in evaluator_classes:
        schemas.append(_get_evaluator_schema(evaluator_class))

    return schemas


def _get_evaluator_schema(evaluator_class):
    """Get schema for a single evaluator class."""
    from apps.custom_actions.schema_utils import resolve_references

    schema = resolve_references(evaluator_class.model_json_schema())
    schema.pop("$defs", None)

    # Remove type ambiguity for optional fields
    for _key, value in schema["properties"].items():
        if "anyOf" in value:
            any_of = value.pop("anyOf")
            value["type"] = [item["type"] for item in any_of if item["type"] != "null"][0]  # take the first type

    evaluator_schema = evaluator_class.model_config.get("evaluator_schema")
    if evaluator_schema:
        schema["label"] = evaluator_schema.label
        schema["icon"] = evaluator_schema.icon

    return schema


def _evaluator_parameter_values(team, llm_providers, llm_provider_models):
    """Returns the possible values for evaluator parameters."""

    def _option(value, label, type_=None, max_token_limit=None):
        data = {"value": value, "label": label}
        data = data | ({"type": type_} if type_ else {})
        data = data | ({"max_token_limit": max_token_limit} if max_token_limit else {})
        return data

    return {
        "LlmProviderId": [_option(provider["id"], provider["name"], provider["type"]) for provider in llm_providers],
        "LlmProviderModelId": [
            _option(provider.id, str(provider), provider.type, provider.max_token_limit)
            for provider in llm_provider_models
        ],
    }


def _evaluator_default_values(llm_providers: list[dict], llm_provider_models):
    """Returns the default values for evaluator parameters."""
    llm_provider_model_id = None
    provider_id = None
    if len(llm_providers) > 0:
        provider = llm_providers[0]
        provider_id = provider["id"]
        first_model = llm_provider_models.filter(type=provider["type"]).first()
        if first_model:
            llm_provider_model_id = first_model.id

    return {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": llm_provider_model_id,
    }
