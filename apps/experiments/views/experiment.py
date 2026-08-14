import logging
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import CharField, Count, F, Prefetch, Q, Subquery, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseGone,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from django.utils.timesince import timesince
from django.views.decorators.cache import cache_control, cache_page
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_tables2 import SingleTableView
from field_audit.models import AuditAction

from apps.analysis.const import LANGUAGE_CHOICES
from apps.analysis.translation import translate_messages_with_llm
from apps.annotations.models import CustomTaggedItem, Tag
from apps.chat.models import ChatMessage
from apps.experiments.const import EMBED_FLOW_REMOVED_ON, EMBED_FLOW_SUCCESSOR_URL
from apps.experiments.decorators import experiment_session_view, require_transcript_access
from apps.experiments.forms import TranslateMessagesForm
from apps.experiments.models import Experiment
from apps.experiments.tables import (
    ExperimentVersionsTable,
)
from apps.experiments.tasks import async_export_chat
from apps.files.models import File
from apps.service_providers.llm_service.default_models import get_default_translation_models_by_provider
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.service_providers.utils import get_models_by_team_grouped_by_provider
from apps.teams.decorators import login_and_team_required, team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.models import Trace


class ExperimentVersionsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = Experiment
    table_class = ExperimentVersionsTable
    template_name = "experiments/experiment_version_table.html"
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        experiment_row = Experiment.objects.get_all().filter(id=self.kwargs["experiment_id"])
        other_versions = Experiment.objects.get_all().filter(working_version=self.kwargs["experiment_id"]).all()
        return (experiment_row | other_versions).order_by("-version_number")


@xframe_options_exempt
@csrf_exempt
@team_required
def embed_flow_gone(request, *args, **kwargs):
    """410 stub for the legacy embedded chat flow, removed on 2026-08-03.

    Serves both the experiments and chatbots embed URLs. Kept for at least one release
    cycle before the URLs are deleted entirely.
    See https://github.com/dimagi/open-chat-studio/issues/3540

    The old views were `@xframe_options_exempt` + `@csrf_exempt`; the stub keeps both so
    legacy callers see the 410 rather than a blocked iframe or a CSRF 403. `@team_required`
    is kept for the team-auth guard in apps/teams/tests/test_view_auth_guard.py — the stub
    reads no data, but the views it replaces were team-scoped and it costs nothing.
    """
    return HttpResponseGone(
        f"The legacy embedded chat flow was removed on {EMBED_FLOW_REMOVED_ON.isoformat()}. "
        f"Use the Open Chat Studio chat widget instead: {EMBED_FLOW_SUCCESSOR_URL}"
    )


@require_POST
@permission_required("experiments.download_chats", raise_exception=True)
@login_and_team_required
def generate_chat_export(request, team_slug: str, experiment_id: str):
    timezone = request.session.get("detected_tz", None)
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    parsed_url = urlparse(request.htmx.current_url)
    task_id = async_export_chat.delay(experiment_id, parsed_url.query, timezone)
    return TemplateResponse(
        request, "experiments/components/exports.html", {"experiment": experiment, "task_id": task_id}
    )


def _get_languages_for_chat(session):
    available_language_codes = session.chat.translated_languages
    available_languages = [
        choice for choice in LANGUAGE_CHOICES if choice[0] == "" or choice[0] in available_language_codes
    ]
    translatable_languages = [
        choice for choice in LANGUAGE_CHOICES if choice[0] != "" and choice[0] not in available_language_codes
    ]
    return available_languages, translatable_languages


def _add_time_gap_info(messages, gap_threshold_hours=4):
    """
    Add time gap information to messages for display in the template.
    Returns a list of messages with time_gap and time_gap_text attributes added.
    """
    threshold = timedelta(hours=gap_threshold_hours)
    enhanced_messages = []

    for i, message in enumerate(messages):
        # Add gap info attributes
        message.time_gap_text = None

        if i > 0:
            prev_message = messages[i - 1]
            time_diff = message.created_at - prev_message.created_at

            if time_diff > threshold:
                message.time_gap_text = f"{timesince(prev_message.created_at, message.created_at)} later"

        enhanced_messages.append(message)

    return enhanced_messages


@login_and_team_required
@experiment_session_view
@require_transcript_access
def experiment_session_messages_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    """View for loading paginated messages with HTMX"""
    session = request.experiment_session
    experiment = request.experiment
    page = int(request.GET.get("page", 1))
    selected_tags = list(filter(None, request.GET.get("tag_filter", "").split(",")))
    language = request.GET.get("language", "")
    show_original_translation = request.GET.get("show_original_translation") == "on" and language
    try:
        highlight_message_id = int(request.GET.get("message_id"))
    except (ValueError, TypeError):
        highlight_message_id = None

    chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)
    all_tags = (
        Tag.objects.filter(
            annotations_customtaggeditem_items__content_type=chat_message_content_type,
            annotations_customtaggeditem_items__object_id__in=Subquery(
                ChatMessage.objects.filter(chat=session.chat).values("id")
            ),
        )
        .annotate(count=Count("annotations_customtaggeditem_items"))
        .distinct()
        .order_by(F("category").asc(nulls_first=True), "name")
    )
    available_languages, translatable_languages = _get_languages_for_chat(session)
    has_missing_translations = False
    translate_form_all = TranslateMessagesForm(
        team=request.team, translatable_languages=translatable_languages, is_translate_all_form=True
    )
    translate_form_remaining = TranslateMessagesForm(
        team=request.team, translatable_languages=translatable_languages, is_translate_all_form=False
    )
    default_message = "(message generated after last translation)"

    messages_queryset = (
        ChatMessage.objects.filter(chat=session.chat)
        .order_by("created_at")
        .prefetch_related(
            Prefetch(
                "tagged_items",
                queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                to_attr="prefetched_tagged_items",
            ),
            Prefetch(
                "output_message_trace",
                queryset=Trace.objects.exclude(
                    Q(participant_data_diff__isnull=True) | Q(participant_data_diff=[])
                ).only("id", "participant_data_diff", "output_message_id"),
                to_attr="prefetched_output_traces_with_diff",
            ),
        )
    )
    if selected_tags:
        messages_queryset = messages_queryset.filter(tags__name__in=selected_tags).distinct()

    if language:
        messages_queryset = messages_queryset.annotate(
            translation=Coalesce(
                KeyTextTransform(language, "translations"),
                Value(default_message),
                output_field=CharField(),
            )
        )
        has_missing_translations = messages_queryset.exclude(**{f"translations__{language}__isnull": False}).exists()
    show_all = request.GET.get("show_all") == "on"
    page_size = 10
    if show_all:
        current_page_messages = list(messages_queryset)
        total_pages = 1
        page_start_index = 1
    else:
        paginator = Paginator(messages_queryset, per_page=page_size, orphans=page_size // 3)

        # on the first load, scroll to the page to focus on a specific message id
        if highlight_message_id and not request.GET.get("page"):
            messages_before = messages_queryset.filter(id__lt=highlight_message_id).count()
            page = (messages_before // page_size) + 1

        # Ensure page is valid
        page = min(page, paginator.num_pages) if paginator.num_pages > 0 else 1
        current_page = paginator.page(page)
        current_page_messages = list(current_page.object_list)
        total_pages = paginator.num_pages
        page_start_index = current_page.start_index()

    # Add time gap information to messages
    current_page_messages = _add_time_gap_info(current_page_messages)

    context = {
        "experiment_session": session,
        "experiment": experiment,
        "messages": current_page_messages,
        "page": page,
        "total_pages": total_pages,
        "total_messages": len(messages_queryset),
        "page_size": page_size,
        "page_start_index": page_start_index,
        "selected_tags": selected_tags,
        "language": language,
        "available_languages": available_languages,
        "available_tags": [t.name for t in Tag.objects.filter(team=request.team, is_system_tag=False).all()],
        "has_missing_translations": has_missing_translations,
        "show_original_translation": show_original_translation,
        "translate_form_all": translate_form_all,
        "translate_form_remaining": translate_form_remaining,
        "default_message": default_message,
        "default_translation_models_by_providers": get_default_translation_models_by_provider(),
        "llm_provider_models_dict": get_models_by_team_grouped_by_provider(request.team),
        "all_tags": all_tags,
        "highlight_message_id": highlight_message_id,
    }

    return TemplateResponse(
        request,
        "experiments/components/session_messages.html",
        context,
    )


@login_and_team_required
@experiment_session_view
@require_transcript_access
def translate_messages_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    session = request.experiment_session
    provider_id = request.POST.get("llm_provider", "")
    model_id = request.POST.get("llm_provider_model", "")
    valid_languages = [choice[0] for choice in LANGUAGE_CHOICES if choice[0]]
    translate_all = request.POST.get("translate_all", "false") == "true"
    if translate_all:
        language = request.POST.get("target_language")
    else:
        language = request.POST.get("language")

    if not language or language not in valid_languages:
        messages.error(request, "No language selected for translation.")
        return redirect_to_messages_view(request, session)
    if not provider_id or not model_id:
        messages.error(request, "No LLM provider model selected.")
        return redirect_to_messages_view(request, session)
    try:
        try:
            llm_provider = LlmProvider.objects.get(id=provider_id, team=request.team)
            llm_provider_model = LlmProviderModel.objects.get(id=model_id)
        except (LlmProvider.DoesNotExist, LlmProviderModel.DoesNotExist):
            messages.error(request, "Selected provider or model not found.")
            return redirect_to_messages_view(request, session)

        messages_to_translate = ChatMessage.objects.filter(chat=session.chat).exclude(
            **{f"translations__{language}__isnull": False}
        )
        if not messages_to_translate.exists():
            messages.info(request, "All messages already have translations for this language.")
            return redirect_to_messages_view(request, session)
        translate_messages_with_llm(
            messages=list(messages_to_translate),
            target_language=language,
            llm_provider=llm_provider,
            llm_provider_model=llm_provider_model,
        )
    except Exception as e:
        logging.exception("Error translating messages")
        messages.error(request, f"Translation failed: {str(e)}")
        return redirect_to_messages_view(request, session)

    return redirect_to_messages_view(request, session)


def redirect_to_messages_view(request, session):
    url = reverse(
        "experiments:experiment_session_messages_view",
        args=[request.team.slug, session.experiment.public_id, session.external_id],
    )
    params = {}
    search = request.POST.get("search", "").strip()
    show_original_translation = request.POST.get("show_original_translation", "")
    language = request.POST.get("language", "")
    params["language"] = language or request.POST.get("target_language", "")
    if search:
        params["search"] = search
    if show_original_translation:
        params["show_original_translation"] = show_original_translation

    if params:
        url += "?" + urlencode(params)

    return HttpResponseRedirect(url)


@team_required
def download_file(request, team_slug: str, session_id: int, pk: int):
    resource = get_object_or_404(
        File, id=pk, team=request.team, chatattachment__chat__experiment_session__id=session_id
    )
    # An empty FileField (no underlying storage) raises ValueError when opened.
    # Treat this the same as a missing file on disk.
    if not resource.file:
        raise Http404()
    try:
        file = resource.file.open()
        return FileResponse(
            file, as_attachment=True, filename=resource.file.name, content_type=resource.content_type or None
        )
    except (FileNotFoundError, ValueError):
        raise Http404() from None


@team_required
def get_image_html(request, team_slug: str, session_id: int, pk: int):
    """Return HTML for displaying an image attachment."""
    resource = get_object_or_404(
        File, id=pk, team=request.team, chatattachment__chat__experiment_session__id=session_id
    )

    if not resource.is_image:
        raise Http404("File is not an image")

    # Avoid rendering an <img> that points to a File row without underlying
    # storage (would trigger ValueError on download).
    if not resource.file:
        raise Http404("File has no associated content")

    # Generate the image URL
    image_url = reverse("experiments:download_file", args=[team_slug, session_id, pk])

    # Return HTML for the image
    html = format_html(
        '<img src="{}" alt="{}" class="max-w-md max-h-64 rounded border shadow-sm mt-2">', image_url, resource.name
    )

    return HttpResponse(html)


@require_POST
@transaction.atomic
@login_and_team_required
def set_default_experiment(request, team_slug: str, experiment_id: int, version_number: int):
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    Experiment.objects.exclude(version_number=version_number).filter(
        team=request.team, working_version_id=experiment_id
    ).update(is_default_version=False, audit_action=AuditAction.AUDIT)
    experiment.is_default_version = True
    experiment.save()
    url = (
        reverse(
            "chatbots:single_chatbot_home",
            kwargs={"team_slug": request.team.slug, "experiment_id": experiment_id},
        )
        + "#versions"
    )
    return redirect(url)


@require_POST
@transaction.atomic
@login_and_team_required
def archive_experiment_version(request, team_slug: str, experiment_id: int, version_number: int):
    """
    Archives a single released version of an experiment, unless it's the default version
    """
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    url = (
        reverse(
            "chatbots:single_chatbot_home",
            kwargs={"team_slug": request.team.slug, "experiment_id": experiment_id},
        )
        + "#versions"
    )
    if experiment.is_default_version:
        return redirect(url)
    experiment.archive()
    return redirect(url)


@require_POST
@transaction.atomic
@login_and_team_required
def update_version_description(request, team_slug: str, experiment_id: int, version_number: int):
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    experiment.version_description = request.POST.get("description", "").strip()
    experiment.save()

    return HttpResponse()


@login_and_team_required
def get_release_status_badge(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    context = {"has_changes": experiment.compare_with_latest(), "experiment": experiment}
    return render(request, "experiments/components/unreleased_badge.html", context)


@cache_control(max_age=settings.EXPERIMENT_TREND_CACHE_TIMEOUT, private=True)
@cache_page(settings.EXPERIMENT_TREND_CACHE_TIMEOUT)
@require_GET
@login_and_team_required
@permission_required("experiments.view_experiment")
def trends_data(request, team_slug: str, experiment_id: int):
    """
    Returns JSON data for the experiment's trend barchart chart.
    """
    experiment = get_object_or_404(Experiment.objects.filter(team=request.team), id=experiment_id)
    try:
        successes, errors = experiment.get_trend_data()
    except Exception:
        logging.exception(f"Error loading barchart data for experiment {experiment_id}")
        return JsonResponse({"error": "Failed to load barchart data", "datasets": []}, status=500)
    data = {"successes": successes, "errors": errors}
    return JsonResponse({"trends": data})


@require_GET
@login_and_team_required
@permission_required("experiments.view_experiment")
def get_experiment_version_names(request, team_slug: str, experiment_id: int):
    """
    Returns JSON data for the filters widget
    """
    experiment = get_object_or_404(Experiment.objects.filter(team=request.team), id=experiment_id)
    try:
        version_names = Experiment.objects.get_version_names(experiment.team, working_version=experiment)
    except Exception:
        logging.exception(f"Error loading version names for experiment {experiment_id}")
        return JsonResponse({"error": "Failed to load barchart data", "datasets": []}, status=500)
    return JsonResponse({"version_names": version_names})
