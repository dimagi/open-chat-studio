import json
import logging
from datetime import timedelta
from decimal import Decimal

import httpx
from django import views as django_views
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST
from django_tables2 import SingleTableView

from apps.channels.models import ChannelPlatform
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.cost_tracking.models import PricingRule, PricingSource, ServiceKind
from apps.evaluations.models import Evaluator
from apps.experiments.models import Experiment
from apps.files.forms import get_file_formset
from apps.files.views import BaseAddFileHtmxView
from apps.service_providers.forms import (
    LlmProviderModelForm,
    PricingOverrideForm,
    WhatsappTestMessageForm,
    whatsapp_number_label,
)
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.models import (
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    MessagingProvider,
    MessagingProviderType,
    VoiceProvider,
    VoiceProviderType,
)
from apps.utils.deletion import get_related_objects

from ..generics.chips import Chip
from ..generics.referenced_objects import render_referenced_objects_modal
from ..teams.decorators import login_and_team_required
from ..teams.mixins import LoginAndTeamRequiredMixin
from .usages import get_provider_usages
from .utils import ServiceProvider, get_available_subtypes, get_service_provider_forms

log = logging.getLogger("ocs.service_providers")

REFRESH_TIMEOUT = timedelta(seconds=2 * MetaCloudAPIService.META_API_TIMEOUT + 60)
_PRICE_PER_1K_FROM_MILLION = Decimal(1) / Decimal(1000)
_FORM_FIELD_TO_KIND = {
    "input_price_per_million_tokens": ServiceKind.LLM_INPUT,
    "output_price_per_million_tokens": ServiceKind.LLM_OUTPUT,
    "cached_input_price_per_million_tokens": ServiceKind.LLM_CACHED_INPUT,
}
ROLE_FILTERS = (("all", "All"), ("chat", "Chat"), ("embedding", "Embedding"), ("custom", "Custom"))


def _lookup_subtype_by_slug(subtype_enum, slug):
    """Find the enum member whose ``str()`` form is ``slug``."""
    for member in subtype_enum:
        if str(member) == slug:
            return member
    raise KeyError(slug)


class ServiceProviderMixin:
    @property
    def provider_type(self) -> ServiceProvider:
        type_ = self.kwargs["provider_type"]
        return ServiceProvider[type_]


class ServiceProviderUsagesMixin(LoginAndTeamRequiredMixin, ServiceProviderMixin, PermissionRequiredMixin):
    def get_permission_required(self):
        return (self.provider_type.get_permission("view"),)

    def get_provider(self):
        return get_object_or_404(self.provider_type.model, team=self.request.team, pk=self.kwargs["pk"])


class ServiceProviderUsagesContentView(ServiceProviderUsagesMixin, django_views.View):
    """The Usages tab's contents.

    Resolving usages fans out over many tables and can take several seconds, so
    the provider edit page renders the tab empty and fetches this over HTMX when
    the tab is first opened.
    """

    template_name = "service_providers/components/usages_content.html"

    def get(self, request, *args, **kwargs):
        provider = self.get_provider()
        return render(
            request,
            self.template_name,
            {
                "provider": provider,
                "provider_type": self.provider_type,
                "usages": get_provider_usages(provider),
            },
        )


class ServiceProviderTableView(  # ty: ignore[invalid-method-override]
    LoginAndTeamRequiredMixin, SingleTableView, ServiceProviderMixin, PermissionRequiredMixin
):
    template_name = "table/single_table.html"

    def get_permission_required(self):
        return (self.provider_type.get_permission("view"),)

    def get_queryset(self):
        return self.provider_type.model.objects.filter(team=self.request.team)

    def get_table_class(self):
        return self.provider_type.table


def matches_blocking_deletion_condition(obj):
    return (getattr(obj, "working_version_id", None) is None) or (getattr(obj, "is_default_version", False) is True)


def _experiment_chip_label(experiment) -> str:
    if experiment.is_working_version:
        return f"{experiment.name} [{experiment.get_version_name()}]"
    return f"{experiment.name} {experiment.get_version_name()} [published]"


@require_http_methods(["DELETE"])
@login_and_team_required
def delete_service_provider(request, team_slug: str, provider_type: str, pk: int):
    provider = ServiceProvider[provider_type]
    if not request.user.has_perm(provider.get_permission("delete")):
        raise PermissionDenied()
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
    related_objects = get_related_objects(service_config)

    if related_objects:
        filtered_objects = [obj for obj in related_objects if matches_blocking_deletion_condition(obj)]
        chips_by_kind = {
            "experiments": [
                Chip(label=_experiment_chip_label(experiment), url=experiment.get_absolute_url())
                for experiment in filtered_objects
                if isinstance(experiment, Experiment)
            ],
            # Evaluators reference the provider by FK. Deleting underneath one leaves it
            # unrunnable — nulled FK, stale id in params — so it blocks like the rest.
            "evaluators": [
                Chip(label=evaluator.name, url=evaluator.get_absolute_url())
                for evaluator in filtered_objects
                if isinstance(evaluator, Evaluator)
            ],
        }
        if any(chips_by_kind.values()):
            return render_referenced_objects_modal("service provider", **chips_by_kind)
    service_config.delete()
    return HttpResponse()


class AddFileToProvider(BaseAddFileHtmxView):
    @transaction.atomic()
    def form_valid(self, form):
        provider = ServiceProvider[self.kwargs["provider_type"]]
        provider = get_object_or_404(provider.model, team=self.request.team, pk=self.kwargs["pk"])
        file = super().form_valid(form)
        provider.add_files([file])
        return file

    def get_delete_url(self, file):
        provider = ServiceProvider[self.kwargs["provider_type"]]
        return reverse(
            "service_providers:delete_file",
            kwargs={
                "team_slug": self.request.team.slug,
                "provider_type": provider.slug,
                "pk": self.kwargs["pk"],
                "file_id": file.id,
            },
        )


@login_required
@permission_required("files.delete_file")
@transaction.atomic()
def remove_file(request, team_slug: str, provider_type: str, pk: int, file_id: int):
    provider = ServiceProvider[provider_type]
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
    service_config.remove_file(file_id)
    return HttpResponse()


def _normalize_config(config: dict) -> dict:
    """Drops blank optional fields cleaned_data fills in even when the user never touched
    them, so an untouched field doesn't look like a change against an older, sparser saved
    config."""
    return {k: v for k, v in config.items() if v}


def _has_config_changed(is_create: bool, old_config: dict | None, obj) -> bool:
    """Whether obj's current config differs from old_config. Always True for a create -
    there's nothing to compare against yet."""
    return is_create or old_config != _normalize_config(obj.config)


def _should_test_connection(obj, is_create: bool, old_config: dict | None) -> bool:
    """Whether the automatic connection test should run for this save.

    Only LLM provider types that have a check to run, and then only when there is something
    to find out: the credentials changed, or they have never passed a check. That second
    clause is what keeps a failed check retryable - saving again re-runs it, with no need to
    edit a credential to force it.
    """
    if not isinstance(obj, LlmProvider) or not obj.supports_connection_test:
        return False
    return _has_config_changed(is_create, old_config, obj) or not obj.credentials_verified


def _can_verify_credentials(provider_type, subtype) -> bool:
    """Whether credentials for this provider type can be verified at all."""
    return provider_type == ServiceProvider.llm and subtype.supports_connection_test


def _should_run_post_create_hook(obj, is_create: bool) -> bool:
    """Whether this save just created a new messaging provider - the one moment its
    post-create hook needs to run."""
    return is_create and isinstance(obj, MessagingProvider)


class CreateServiceProvider(
    LoginAndTeamRequiredMixin, django_views.View, ServiceProviderMixin, PermissionRequiredMixin
):
    template_name = "service_providers/provider_form.html"

    def get_permission_required(self):
        if self.kwargs.get("pk"):
            return (self.provider_type.get_permission("change"),)
        return (self.provider_type.get_permission("add"),)

    def _resolve_subtype(self):
        instance = self._get_instance()
        subtype_enum = self.provider_type.subtype
        if instance:
            return _lookup_subtype_by_slug(subtype_enum, instance.type)
        slug = self.kwargs.get("subtype")
        try:
            subtype = _lookup_subtype_by_slug(subtype_enum, slug)
        except KeyError as exc:
            raise Http404(f"Unknown subtype: {slug}") from exc
        if subtype not in get_available_subtypes(self.provider_type, self.request):
            raise Http404("Subtype is not enabled")
        return subtype

    def _get_instance(self):
        if not self.kwargs.get("pk"):
            return None
        return get_object_or_404(self.provider_type.model, team=self.request.team, pk=self.kwargs["pk"])

    def _template(self):
        if self.provider_type == ServiceProvider.llm:
            return "service_providers/llm_provider_form.html"
        return self.template_name

    def get(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, instance=instance
        )
        return render(request, self._template(), self._get_context(primary_form, config_form, subtype, instance))

    def post(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, data=request.POST, instance=instance
        )

        file_formset = None
        if request.FILES:
            file_formset = get_file_formset(request, formset_cls=config_form.file_formset_form)

        # Call is_valid() on every form before combining to avoid short-circuiting
        # away from populating the later forms' errors.
        primary_valid = primary_form.is_valid()
        config_valid = config_form.is_valid()
        file_formset_valid = not file_formset or file_formset.is_valid()
        if primary_valid and config_valid and file_formset_valid:
            obj, had_connection_test_warning = self._save_provider(request, primary_form, config_form, file_formset)
            return HttpResponseRedirect(self.get_success_url(obj, had_connection_test_warning))

        if file_formset and not file_formset.is_valid():
            messages.error(request, ", ".join(file_formset.non_form_errors()))
        return render(request, self._template(), self._get_context(primary_form, config_form, subtype, instance))

    def _save_provider(self, request, primary_form, config_form, file_formset):
        """Saves the provider and returns (obj, had_connection_test_warning).

        The second value tells get_success_url() whether the automatic connection test just
        failed - if so, it sends the user to this provider's own edit page, where the banner
        carrying the provider's own response is, instead of the team list every other save
        redirects to.
        """
        with transaction.atomic():
            obj = primary_form.save(commit=False)
            obj.team = request.team
            is_create = obj.pk is None
            # Captured before config_form.save() overwrites obj.config, so the check below
            # can tell whether credentials actually changed - re-testing on an unrelated
            # edit (e.g. renaming the provider) wastes an external call for no reason.
            old_config = None if is_create else _normalize_config(obj.config)
            config_form.save(obj)
            obj.save()
            if file_formset:
                files = file_formset.save(request)
                obj.add_files(files)
            if isinstance(obj, VoiceProvider):
                for warning in obj.run_post_save_hook():
                    messages.warning(request, warning)
            if _should_run_post_create_hook(obj, is_create):
                obj.run_post_create_hook()
        # Runs after the save transaction commits, not inside it: an external LLM call can
        # take several seconds, and holding the save's DB connection/locks open for that long
        # (or repeating the call if the transaction were retried or rolled back) is worse than
        # the save and the test being two separate steps.
        had_connection_test_warning = False
        if _should_test_connection(obj, is_create, old_config):
            warnings, _detail = obj.run_connection_test_hook()
            for warning in warnings:
                messages.warning(request, warning)
                had_connection_test_warning = True
        for warning in config_form.warnings:
            messages.warning(request, warning)
        return obj, had_connection_test_warning

    def _button_text(self, instance, subtype) -> str:
        """Says up front whether saving will also verify credentials.

        On an existing provider that is the stored flag; the template flips the label
        reactively on top of this when the user edits a credential field.
        """
        verb = "Update" if instance else "Create"
        if not _can_verify_credentials(self.provider_type, subtype):
            return verb
        if instance and instance.credentials_verified:
            return verb
        return f"{verb} and Verify"

    def _get_context(self, primary_form, config_form, subtype, instance):
        can_view_usages = self.request.user.has_perm(self.provider_type.get_permission("view"))
        ctx = {
            "primary_form": primary_form,
            "config_form": config_form,
            "provider": self.provider_type,
            "subtype": subtype,
            "object": instance,
            "title": f"Edit {instance.name}" if instance else self.provider_type.label,
            # For an existing LLM provider this is only the pre-Alpine-hydration default -
            # the template overrides it reactively based on whether credentials changed.
            "button_text": self._button_text(instance, subtype),
            "active_tab": "manage-team",
            "active_provider_tab": _active_provider_tab(self.request, self.provider_type, instance, can_view_usages),
            "can_view_usages": can_view_usages,
        }
        is_elevenlabs_voice = (
            isinstance(instance, VoiceProvider) and instance.type == VoiceProviderType.elevenlabs.value
        )
        if is_elevenlabs_voice:
            ctx["sync_voices_url"] = reverse(
                "service_providers:sync_voices",
                kwargs={
                    "team_slug": self.request.team.slug,
                    "provider_type": "voice",
                    "pk": instance.pk,
                },
            )
        if isinstance(instance, MessagingProvider) and instance.type == MessagingProviderType.meta_cloud_api:
            ctx["whatsapp_status_url"] = reverse(
                "service_providers:whatsapp_status",
                kwargs={"team_slug": self.request.team.slug, "pk": instance.pk},
            )
        if self.provider_type == ServiceProvider.llm:
            ctx["can_verify_credentials"] = bool(instance) and _can_verify_credentials(self.provider_type, subtype)
            ctx["new_model_form"] = LlmProviderModelForm(self.request.team)
            if instance:
                ctx["verification_error"] = instance.verification_error
                ctx["credentials_verified"] = instance.credentials_verified
            ctx.update(llm_models_context(self.request.team, subtype))
        return ctx

    def get_success_url(self, obj=None, redirect_to_own_edit_page=False):
        """Normally back to the team list, same as every save today. The one exception: a
        failed verification sends the user back to this provider's own edit page, where the
        credentials the warning is about are the ones on screen.
        """
        if redirect_to_own_edit_page and obj is not None:
            return resolve_url(
                "service_providers:edit",
                team_slug=self.request.team.slug,
                provider_type=self.provider_type.slug,
                pk=obj.pk,
            )
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)


def _active_provider_tab(request, provider_type, instance, can_view_usages: bool) -> str:
    """The tab to open, limited to the ones this page actually renders.

    A tab strip whose radio group has nothing checked hides every panel, so naming a tab the
    page does not have would leave the form invisible rather than just unselected.
    """
    available = {"configuration"}
    if instance:
        if provider_type == ServiceProvider.llm:
            available.add("models")
        if can_view_usages:
            available.add("usages")
    tab = request.GET.get("tab")
    return tab if tab in available else "configuration"


def _model_row(model, rates, is_embedding: bool) -> dict:
    return {
        "id": model.id,
        # LlmProviderModel and EmbeddingProviderModel are separate tables with separate
        # sequences, so their ids collide - the row needs one that is unique per rendered
        # list, or an hx-target resolves to whichever row querySelector reaches first.
        "dom_id": f"{'embedding' if is_embedding else 'model'}_{model.id}",
        "name": model.name,
        "role": "embedding" if is_embedding else "chat",
        "custom": model.team_id is not None,
        "deprecated": getattr(model, "deprecated", False),
        "context": None if is_embedding else _format_context(model.max_token_limit),
        "rates": rates,
        "is_llm": not is_embedding,
    }


def _format_context(max_token_limit: int) -> str:
    """Token limits are read as magnitudes, not counted, so 409600 is noise next to 400K.

    Zero is not a small limit - it's the value that disables compression entirely.
    """
    if not max_token_limit:
        return "—"
    if max_token_limit >= 1_000_000:
        return f"{max_token_limit / 1_000_000:.1f}".removesuffix(".0") + "M"
    if max_token_limit >= 1_000:
        return f"{round(max_token_limit / 1_000)}K"
    return str(max_token_limit)


def llm_models_context(team, subtype) -> dict:
    """The Models tab: every model this provider type can use, as one list.

    Chat and embedding models, team-owned and global, are one list because they answer one
    question - what can this provider run? - and the role badge carries the rest. The rows
    are keyed on provider type plus team rather than on the provider, so every provider of
    the same type shows the same list.
    """
    type_slug = str(subtype)
    llm_models = list(LlmProviderModel.objects.for_team(team).filter(type=type_slug))
    embedding_models = list(EmbeddingProviderModel.objects.for_team(team).filter(type=type_slug))
    pricing_lookup = _pricing_lookup(team, llm_models)

    rows = [
        _model_row(model, rates=pricing_lookup.get(model.id), is_embedding=is_embedding)
        for models_, is_embedding in ((llm_models, False), (embedding_models, True))
        for model in models_
    ]
    # Deprecated models sink to the bottom; chat before embedding, then alphabetical.
    rows.sort(key=lambda r: (r["deprecated"], r["role"] == "embedding", r["name"]))
    return {
        "model_rows": rows,
        "role_filters": ROLE_FILTERS,
        "deprecated_count": sum(1 for r in rows if r["deprecated"]),
    }


def _pricing_lookup(team, llm_models: list) -> dict:
    """`{model_id: {service_kind: {unit_price, source, scope}, ...}}` for
    every model with at least one active rule. Single bulk query;
    team-scoped rules overwrite global ones for the same key.
    """
    if not llm_models:
        return {}
    rules = list(
        PricingRule.objects.filter(
            Q(team=team) | Q(team__isnull=True),
            provider_type__in={m.type for m in llm_models},
            model_name__in={m.name for m in llm_models},
            effective_to__isnull=True,
        )
    )
    by_key = _index_rules_by_provider_model(rules)
    global_keys = {(r.provider_type, r.model_name) for r in rules if r.team_id is None}
    for key, rates in by_key.items():
        _augment_with_template_helpers(rates, has_global_rate=key in global_keys)
    return {m.id: by_key[(m.type, m.name)] for m in llm_models if (m.type, m.name) in by_key}


def _index_rules_by_provider_model(rules) -> dict[tuple[str, str], dict]:
    """Globals first so team-scoped rules overwrite them on the same key."""
    by_key: dict[tuple[str, str], dict] = {}
    for rule in sorted(rules, key=lambda r: r.team_id is not None):
        key = (rule.provider_type, rule.model_name)
        by_key.setdefault(key, {})[rule.service_kind] = {
            "unit_price": rule.unit_price,
            "source": rule.source,
            "scope": "team" if rule.team_id else "global",
        }
    return by_key


def _augment_with_template_helpers(rates: dict, *, has_global_rate: bool) -> None:
    """Mutates `rates` in place with two synthetic keys the template reads
    directly: `primary` (input rate, falling back to output) and
    `can_revert` (for the Revert button). Avoids the Django template
    `|default:` gotcha on missing dict keys under strict resolution.

    Reverting closes the team's rules and re-resolves against the global ones, so it is
    only offered when a global rule exists. A custom model has none - reverting there would
    leave the model unpriced, which is a delete, not a revert.
    """
    primary = rates.get(ServiceKind.LLM_INPUT.value) or rates.get(ServiceKind.LLM_OUTPUT.value)
    if primary:
        rates["primary"] = primary
    has_team_override = any(r["scope"] == "team" for r in rates.values() if isinstance(r, dict))
    rates["can_revert"] = has_team_override and has_global_rate


@require_POST
@login_and_team_required
@permission_required("service_providers.add_llmprovidermodel")
def create_llm_provider_model(request, team_slug: str):
    form = LlmProviderModelForm(request.team, request.POST)
    if not form.is_valid():
        if len(form.errors) == 1 and "__all__" in form.errors:
            return HttpResponseBadRequest(", ".join([str(v) for v in form.errors.values()]))
        return HttpResponseBadRequest(str(form.errors))
    with transaction.atomic():
        model = form.save(commit=False)
        model.team = request.team
        model.save()
        _persist_team_pricing_rules(request.team, model, form.cleaned_data, request.user)
    return render(
        request,
        "service_providers/components/llm_model_rows.html",
        llm_models_context(request.team, form.cleaned_data["type"]),
    )


class PricingOverrideView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, django_views.View):
    """GET renders the override modal; POST persists team-scoped rules.
    Invalid submissions re-render the form (with field errors) into the modal
    body via HX-Retarget so the user can correct in place."""

    template_name = "service_providers/components/pricing_override_form.html"
    permission_required = "service_providers.change_llmprovidermodel"
    raise_exception = True

    def get(self, request, team_slug: str, pk: int):
        model = _resolve_model(request.team, pk)
        form = PricingOverrideForm(initial=_form_initial_from_active_rates(request.team, model))
        return render(request, self.template_name, {"form": form, "model": model})

    def post(self, request, team_slug: str, pk: int):
        model = _resolve_model(request.team, pk)
        form = PricingOverrideForm(request.POST)
        if not form.is_valid():
            response = render(request, self.template_name, {"form": form, "model": model})
            response.status_code = 400
            response["HX-Retarget"] = "#pricing_override_modal_body"
            response["HX-Reswap"] = "innerHTML"
            return response
        with transaction.atomic():
            _persist_team_pricing_rules(request.team, model, form.cleaned_data, request.user)
        return _render_model_row(request, model)


@require_POST
@login_and_team_required
@permission_required("service_providers.change_llmprovidermodel", raise_exception=True)
def pricing_revert(request, team_slug: str, pk: int):
    """Close every active team-scoped rule for this (provider, model_name).
    Resolution falls back to the matching global rule on the next read."""
    model = _resolve_model(request.team, pk)
    PricingRule.objects.filter(
        team=request.team,
        provider_type=model.type,
        model_name=model.name,
        effective_to__isnull=True,
    ).update(effective_to=timezone.now())
    return _render_model_row(request, model)


def _resolve_model(team, pk: int) -> LlmProviderModel:
    """Both team-scoped customs and the global defaults are addressable."""
    return get_object_or_404(LlmProviderModel, Q(team=team) | Q(team__isnull=True), pk=pk)


def _form_initial_from_active_rates(team, model: LlmProviderModel) -> dict:
    """Pre-fill the override form with the currently resolved per-million rate
    for each service kind (team override wins over global)."""
    lookup = _pricing_lookup(team, [model])
    rates = lookup.get(model.id, {})
    initial: dict[str, str] = {}
    for field, kind in _FORM_FIELD_TO_KIND.items():
        rate = rates.get(kind.value)
        if rate:
            initial[field] = _format_per_million(rate["unit_price"])
    return initial


def _format_per_million(unit_price: Decimal) -> str:
    """Convert a per-1K unit price to its per-1M display string. Plain
    decimal - `.normalize()` collapses whole numbers to scientific notation
    (e.g. `30` → `3E+1`), which the form input renders verbatim."""
    text = format(unit_price * Decimal(1000), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _persist_team_pricing_rules(team, model: LlmProviderModel, cleaned: dict, user) -> None:
    """Close any active team rule for the (provider, model, kind) and insert
    a fresh team-scoped rule per non-empty form field. Globals are untouched
    - resolution merges them with the team override at read time. The UPDATE
    takes per-row exclusive locks under Postgres READ COMMITTED, so concurrent
    overrides serialise on the active row and the partial-unique active-rule
    constraint can't trip."""
    now = timezone.now()
    with transaction.atomic():
        for field_name, kind in _FORM_FIELD_TO_KIND.items():
            per_million = cleaned.get(field_name)
            if per_million is None:
                continue
            PricingRule.objects.filter(
                team=team,
                provider_type=model.type,
                model_name=model.name,
                service_kind=kind,
                effective_to__isnull=True,
            ).update(effective_to=now)
            PricingRule.objects.create(
                team=team,
                provider_type=model.type,
                model_name=model.name,
                service_kind=kind,
                unit_price=per_million * _PRICE_PER_1K_FROM_MILLION,
                source=PricingSource.MANUAL,
                created_by=user,
            )


def _render_model_row(request, model: LlmProviderModel) -> HttpResponse:
    """Re-render a single row partial after an HTMX swap."""
    rates = _pricing_lookup(request.team, [model]).get(model.id)
    return render(
        request,
        "service_providers/components/llm_model_row.html",
        {"row": _model_row(model, rates=rates, is_embedding=False)},
    )


@require_http_methods(["DELETE"])
@login_required
@permission_required("service_providers.delete_llmprovidermodel")
def delete_llm_provider_model(request, team_slug: str, pk: int):
    llm_provider_model = get_object_or_404(LlmProviderModel, team=request.team, pk=pk)
    try:
        llm_provider_model.delete()
    except ValidationError as ex:
        return HttpResponseBadRequest(", ".join(ex.messages).encode("utf-8"))
    return HttpResponse()


@require_POST
@login_and_team_required
@permission_required("service_providers.change_voiceprovider", raise_exception=True)
def sync_voices(request, team_slug: str, provider_type: str, pk: int):
    provider = get_object_or_404(VoiceProvider, team=request.team, pk=pk)
    try:
        provider.sync_voices()
        count = provider.syntheticvoice_set.count()
        messages.success(request, f"Voices synced successfully. {count} voice(s) available.")
    except Exception:
        log.exception("Failed to sync voices for provider %s", pk)
        messages.error(request, "Voice sync failed. Please check your API key and try again.")
    return redirect("service_providers:edit", team_slug=team_slug, provider_type=provider_type, pk=pk)


def _sync_in_flight(provider: MessagingProvider) -> bool:
    """Is a refresh running, and started recently enough to still be worth waiting for?"""
    started_at = parse_datetime(provider.whatsapp_refresh_info.get("started_at") or "")
    if not started_at:
        return False
    return timezone.now() - started_at < REFRESH_TIMEOUT


def _whatsapp_status_context(provider: MessagingProvider) -> dict:
    """Everything the WhatsApp panel renders, all of it read from the provider's cache.

    Nothing here calls Meta. The refresh button is the only thing that does, by way of
    ``sync_whatsapp_provider_task``, and the panel polls itself until that lands.
    """
    numbers_info = provider.whatsapp_numbers_info
    template_info = provider.whatsapp_template_info
    numbers = provider.whatsapp_numbers
    syncing = _sync_in_flight(provider)
    initial_message = f"Test message from Open Chat Studio for {provider.name}."
    return {
        "provider": provider,
        "numbers": numbers,
        "syncing": syncing,
        "stalled": bool(provider.whatsapp_refresh_info) and not syncing,
        "numbers_sync_error": None if syncing else numbers_info.get("error"),
        "synced_at": parse_datetime(numbers_info.get("synced_at") or ""),
        "form": WhatsappTestMessageForm(numbers, initial={"message": initial_message}),
        "message_length": len(initial_message),
        "message_limit": MetaCloudAPIService.TEMPLATE_MESSAGE_CHAR_LIMIT,
        # The cached TemplateCheck, as a plain dict -- the template reads the same attributes
        # either way. Empty means nobody has checked yet, which is not the same as a failure.
        "template_check": template_info,
        "template_checked": bool(template_info),
        "template_ok": template_info.get("ok") is True,
        "template_checked_at": parse_datetime(template_info.get("checked_at") or ""),
        "template_name": MetaCloudAPIService.TEMPLATE_NAME,
        "template_parameter": MetaCloudAPIService.TEMPLATE_PARAMETER,
    }


@login_and_team_required
@permission_required("service_providers.change_messagingprovider", raise_exception=True)
def whatsapp_status(request, team_slug: str, pk: int):
    """The template check and test-send panel, loaded into the provider page by HTMX.

    Renders from the cache and polls itself while a refresh is running, so opening the
    provider page never waits on Meta.
    """
    provider = get_object_or_404(MessagingProvider, team=request.team, pk=pk, type=MessagingProviderType.meta_cloud_api)
    context = _whatsapp_status_context(provider)
    return render(request, "service_providers/components/whatsapp_provider_status.html", context)


@require_POST
@login_and_team_required
@permission_required("service_providers.change_messagingprovider", raise_exception=True)
def whatsapp_refresh(request, team_slug: str, pk: int):
    """Re-fetch this provider's numbers and re-check its message template."""
    provider = get_object_or_404(MessagingProvider, team=request.team, pk=pk, type=MessagingProviderType.meta_cloud_api)
    if not _sync_in_flight(provider):
        provider.queue_whatsapp_provider_sync()
    context = _whatsapp_status_context(provider)
    return render(request, "service_providers/components/whatsapp_provider_status.html", context)


@require_POST
@login_and_team_required
@permission_required("service_providers.change_messagingprovider", raise_exception=True)
def whatsapp_send_test(request, team_slug: str, pk: int):
    """Send one template message through the provider and report exactly what Meta said."""
    provider = get_object_or_404(MessagingProvider, team=request.team, pk=pk, type=MessagingProviderType.meta_cloud_api)
    numbers = provider.whatsapp_numbers
    form = WhatsappTestMessageForm(numbers, data=request.POST)
    if not form.is_valid():
        errors = [message for field_errors in form.errors.values() for message in field_errors]
        return render(request, "service_providers/components/whatsapp_test_result.html", {"errors": errors})

    from_number_id = form.cleaned_data["from_number_id"]
    to_number = form.cleaned_data["to_number"]
    context = {}
    try:
        provider.get_messaging_service().send_template_message(
            message=form.cleaned_data["message"],
            from_=from_number_id,
            to=to_number,
            platform=ChannelPlatform.WHATSAPP,
        )
    except ServiceWindowExpiredException as exc:
        context["error_message"] = str(exc)
    except httpx.HTTPStatusError as exc:
        context["error_status"] = exc.response.status_code
        context["error_body"] = _format_meta_error_body(exc.response.text)
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the operator needs to see it
        log.exception("Test WhatsApp message failed for provider %s", pk)
        context["error_message"] = str(exc) or exc.__class__.__name__
    else:
        selected = next((number for number in numbers if number["phone_number_id"] == from_number_id), None)
        context["sent_to"] = to_number
        context["sent_from"] = whatsapp_number_label(selected) if selected else from_number_id
    return render(request, "service_providers/components/whatsapp_test_result.html", context)


def _format_meta_error_body(body: str) -> str:
    """Pretty-print Meta's error response so it is readable in the panel."""
    try:
        return json.dumps(json.loads(body), indent=2)
    except ValueError:
        return body[:2000]
