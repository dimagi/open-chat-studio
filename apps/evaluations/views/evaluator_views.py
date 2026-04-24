import inspect
from functools import lru_cache

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.custom_actions.schema_utils import resolve_references
from apps.evaluations import evaluators
from apps.evaluations.forms import EvaluatorForm, EvaluatorTagRuleFormSet
from apps.evaluations.models import Evaluator
from apps.evaluations.tables import EvaluatorTable
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.web.waf import WafRule, waf_allow


class EvaluatorHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "evaluations.view_evaluator"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "evaluators",
            "title": "Evaluators",
            "page_title": "Evaluators",
            "new_object_url": reverse("evaluations:evaluator_new", args=[team_slug]),
            "table_url": reverse("evaluations:evaluator_table", args=[team_slug]),
        }


class EvaluatorTableView(PermissionRequiredMixin, SingleTableView):
    permission_required = "evaluations.view_evaluator"
    model = Evaluator
    table_class = EvaluatorTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            Evaluator.objects.filter(team=self.request.team)
            # .annotate(run_count=Count("runs"))
            # .order_by("name")
        )


class EvaluatorFormsetMixin:
    """Shared formset plumbing for Create/Edit evaluator views."""

    def _build_tag_rule_formset(self, instance, data=None):
        return EvaluatorTagRuleFormSet(
            data=data,
            instance=instance,
            team=self.request.team,
            output_schema=(instance.params or {}).get("output_schema", {}) if instance else {},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "tag_rule_formset" not in context:
            context["tag_rule_formset"] = self._build_tag_rule_formset(context.get("object"))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object_or_none()
        form = self.get_form()
        formset = self._build_tag_rule_formset(self.object or Evaluator(team=request.team), data=request.POST)

        if form.is_valid():
            evaluator = form.save(commit=False)
            evaluator.team = request.team
            # Re-bind formset against the pending-save evaluator so nested validation has team + schema.
            formset = EvaluatorTagRuleFormSet(
                data=request.POST,
                instance=evaluator,
                team=request.team,
                output_schema=(form.cleaned_data.get("params") or {}).get("output_schema", {}),
            )
            if formset.is_valid():
                with transaction.atomic():
                    evaluator.save()
                    formset.instance = evaluator
                    formset.save()
                return redirect(self.get_success_url())
        else:
            formset.is_valid()  # surface errors in the template

        return self.render_to_response(self.get_context_data(form=form, tag_rule_formset=formset))

    def get_object_or_none(self):
        return None

    def _get_evaluator_form_context(self):
        llm_providers = list(LlmProvider.objects.filter(team=self.request.team).values("id", "name", "type"))
        llm_provider_models = list(LlmProviderModel.objects.for_team(self.request.team))
        return {
            "evaluator_schemas": _evaluator_schemas(),
            "parameter_values": _evaluator_parameter_values(self.request.team, llm_providers, llm_provider_models),
            "default_values": _evaluator_default_values(llm_providers, llm_provider_models),
        }


@waf_allow(WafRule.SizeRestrictions_BODY)
class CreateEvaluator(LoginAndTeamRequiredMixin, PermissionRequiredMixin, EvaluatorFormsetMixin, CreateView):
    permission_required = "evaluations.add_evaluator"
    template_name = "evaluations/evaluator_form.html"
    model = Evaluator
    form_class = EvaluatorForm
    extra_context = {
        "title": "Create Evaluator",
        "page_title": "Create Evaluator",
        "button_text": "Create",
        "active_tab": "evaluators",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_evaluator_form_context())
        return context

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:evaluator_home", args=[self.request.team.slug])


class EditEvaluator(LoginAndTeamRequiredMixin, PermissionRequiredMixin, EvaluatorFormsetMixin, UpdateView):
    permission_required = "evaluations.change_evaluator"
    model = Evaluator
    form_class = EvaluatorForm
    template_name = "evaluations/evaluator_form.html"
    extra_context = {
        "title": "Update Evaluator",
        "page_title": "Update Evaluator",
        "button_text": "Update",
        "active_tab": "evaluators",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_evaluator_form_context())
        return context

    def get_queryset(self):
        return Evaluator.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_object_or_none(self):
        return self.get_object()

    def get_success_url(self):
        return reverse("evaluations:evaluator_home", args=[self.request.team.slug])


class DeleteEvaluator(LoginAndTeamRequiredMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "evaluations.delete_evaluator"
    model = Evaluator

    def get_queryset(self):
        return Evaluator.objects.filter(team=self.request.team)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return HttpResponse(status=200)


@lru_cache(maxsize=1)
def _evaluator_schemas():
    """Returns schemas for all available evaluator classes."""
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
    if llm_providers:
        provider = llm_providers[0]
        provider_id = provider["id"]
        first_model = next((m for m in llm_provider_models if m.type == provider["type"]), None)
        if first_model:
            llm_provider_model_id = first_model.id

    return {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": llm_provider_model_id,
    }
