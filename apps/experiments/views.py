import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import markdown
import pytz
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.channels.models import ChannelSession, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.experiments.decorators import experiment_session_view
from apps.experiments.email import send_experiment_invitation
from apps.experiments.export import experiment_to_csv
from apps.experiments.forms import ConsentForm, ExperimentInvitationForm, SurveyForm
from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    Participant,
    Prompt,
    PromptBuilderHistory,
    SessionStatus,
    SourceMaterial,
)
from apps.experiments.tasks import get_prompt_builder_response_task, get_response_for_webchat_task
from apps.teams.decorators import login_and_team_required, team_admin_required
from apps.teams.models import Team
from apps.users.models import CustomUser


@login_and_team_required
def experiments_home(request, team_slug: str):
    experiments = Experiment.objects.filter(Q(is_active=True) | Q(owner=request.user)).order_by("-created_at")
    return TemplateResponse(
        request,
        "experiments/experiment_home.html",
        {
            "experiments": experiments,
            "active_tab": "experiments",
        },
    )


@login_and_team_required
def prompt_builder_load_prompts(request, team_slug: str):
    prompts = Prompt.objects.all()
    prompts_list = list(prompts.values())

    return TemplateResponse(
        request,
        "experiments/prompts_list.html",
        {
            "prompts": prompts_list,
        },
    )


@login_and_team_required
def prompt_builder_load_source_material(request, team_slug: str):
    source_material = SourceMaterial.objects.all()
    source_material_list = list(source_material.values())

    return TemplateResponse(
        request,
        "experiments/source_material_list.html",
        {
            "source_materials": source_material_list,
        },
    )


@login_and_team_required
def experiments_prompt_builder(request, team_slug: str):
    prompts = Prompt.objects.order_by("-created_at").all()
    prompts_list = list(prompts.values())

    return TemplateResponse(
        request,
        "experiments/prompt_builder.html",
        {
            "prompts": prompts_list,
            "active_tab": "prompt_builder",
        },
    )


@require_POST
@login_and_team_required
def experiments_prompt_builder_get_message(request, team_slug: str):
    data_json = request.body.decode("utf-8")
    user = get_real_user_or_none(request.user)
    result = get_prompt_builder_response_task.delay(user.id, data_json)
    return JsonResponse({"task_id": result.task_id})


def get_prompt_builder_message_response(request, team_slug: str):
    task_id = request.GET.get("task_id")
    progress = Progress(AsyncResult(task_id))
    return JsonResponse(
        {
            "task_id": task_id,
            "progress": progress.get_info(),
        },
    )


@login_and_team_required
def get_prompt_builder_history(request, team_slug: str):
    # Fetch history for the request user limited to last 30 days
    thirty_days_ago = timezone.now() - timedelta(days=30)
    histories = PromptBuilderHistory.objects.filter(owner=request.user, created_at__gte=thirty_days_ago).order_by(
        "-created_at"
    )

    # Initialize temporary output format
    output_temp = defaultdict(list)

    history_id = 0  # Initial history_id for the oldest item
    for history in histories:
        # Adding history_id to JSON history object
        history_data = history.history
        history_data["history_id"] = history_id

        # Customizing the output for each history
        event = {
            "history_id": history_id,
            "time": history.created_at.strftime("%H:%M"),
            "preview": history_data.get("preview", ""),
            "sourceMaterialName": history_data.get("sourceMaterialName", "None"),
            "sourceMaterialID": history_data.get("sourceMaterialID", -1),
            "temperature": history_data.get("temperature", 0.7),
            "prompt": history_data.get("prompt", ""),
            "inputFormatter": history_data.get("inputFormatter", ""),
            "model": history_data.get("model", "gpt-4"),
            "messages": history_data.get("messages", []),
        }

        # Populating the temporary output dictionary
        output_temp[history.created_at.date()].append(event)

        history_id += 1  # Incrementing history_id for each newer history

    # Convert to the final desired output format
    output_list = [
        {"date": date_obj.strftime("%A %d %b %Y"), "events": events} for date_obj, events in output_temp.items()
    ]

    return JsonResponse(output_list, safe=False)


@login_and_team_required
@team_admin_required
def prompt_builder_start_save_process(request, team_slug: str):
    # Get your long data
    long_data = json.loads(request.body)

    # Save it in session
    request.session["long_data"] = long_data

    # Redirect to admin add page
    return JsonResponse({"redirect_url": reverse("admin:experiments_prompt_add")})


@login_and_team_required
def single_experiment_home(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    sessions = ExperimentSession.objects.filter(
        user=request.user,
        experiment=experiment,
    )
    return TemplateResponse(
        request,
        "experiments/single_experiment_home.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "sessions": sessions,
        },
    )


def _start_experiment_session(
    experiment: Experiment, user: Optional[CustomUser] = None, participant: Optional[Participant] = None
) -> ExperimentSession:
    session = ExperimentSession.objects.create(
        user=user,
        participant=participant,
        experiment=experiment,
        llm=experiment.llm,
    )
    return _check_and_process_seed_message(session)


def _check_and_process_seed_message(session: ExperimentSession):
    if session.experiment.seed_message:
        session.seed_task_id = get_response_for_webchat_task.delay(
            session.id, message_text=session.experiment.seed_message
        ).task_id
        session.save()
    return session


@require_POST
@login_and_team_required
def start_session(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    channel = _ensure_channel_exists(experiment=experiment, platform="web", name=f"{experiment.id}-web")
    session = _start_experiment_session(experiment, request.user)
    # TODO: Move this into `_start_experiment_session` (or some model?) and refactor
    ChannelSession.objects.get_or_create(
        experiment_channel=channel, experiment_session=session, external_chat_id=session.chat.id
    )
    return HttpResponseRedirect(
        reverse("experiments:experiment_chat_session", args=[team_slug, experiment_id, session.id])
    )


def _ensure_channel_exists(experiment: Experiment, platform: str, name: str) -> ExperimentChannel:
    channel, _created = ExperimentChannel.objects.get_or_create(experiment=experiment, platform=platform, name=name)
    return channel


@login_and_team_required
def experiment_chat_session(request, team_slug: str, experiment_id: int, session_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    session = get_object_or_404(ExperimentSession, user=request.user, experiment_id=experiment_id, id=session_id)
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": experiment,
            "session": session,
            "active_tab": "experiments",
        },
    )


@require_POST
def experiment_session_message(request, team_slug: str, experiment_id: int, session_id: int):
    message_text = request.POST["message"]
    experiment = get_object_or_404(Experiment, id=experiment_id)
    # hack for anonymous user/teams
    user = get_real_user_or_none(request.user)
    session = get_object_or_404(ExperimentSession, user=user, experiment_id=experiment_id, id=session_id)
    result = get_response_for_webchat_task.delay(session.id, message_text)
    return TemplateResponse(
        request,
        "experiments/chat/experiment_response_htmx.html",
        {
            "experiment": experiment,
            "session": session,
            "message_text": message_text,
            "task_id": result.task_id,
        },
    )


# @login_and_team_required
def get_message_response(request, team_slug: str, experiment_id: int, session_id: int, task_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    # hack for anonymous user/teams
    user = get_real_user_or_none(request.user)
    session = get_object_or_404(ExperimentSession, user=user, experiment_id=experiment_id, id=session_id)
    last_message = ChatMessage.objects.filter(chat=session.chat).order_by("-created_at").first()
    progress = Progress(AsyncResult(task_id))
    return TemplateResponse(
        request,
        "experiments/chat/chat_message_response.html",
        {
            "experiment": experiment,
            "session": session,
            "task_id": task_id,
            "progress": progress.get_info(),
            "last_message_datetime": last_message and quote(last_message.created_at.isoformat()),
        },
    )


def poll_messages(request, team_slug: str, experiment_id: int, session_id: int):
    user = get_real_user_or_none(request.user)
    params = request.GET.dict()
    since_param = params.get("since")
    experiment_session = get_object_or_404(ExperimentSession, user=user, experiment_id=experiment_id, id=session_id)

    since = datetime.now().astimezone(pytz.timezone("UTC"))
    if since_param and since_param != "null":
        try:
            since = datetime.fromisoformat(since_param)
        except ValueError as e:
            logging.exception(f"Unexpected `since` parameter value. Error: {e}")

    messages = (
        ChatMessage.objects.filter(message_type="ai", chat=experiment_session.chat, created_at__gt=since)
        .order_by("created_at")
        .all()
    )
    last_message = messages[0] if messages else None

    return TemplateResponse(
        request,
        "experiments/chat/system_message.html",
        {
            "messages": [message.content for message in messages],
            "last_message_datetime": last_message and quote(last_message.created_at.isoformat()),
        },
    )


def start_experiment(request, team_slug: str, experiment_id: str):
    try:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, is_active=True)
    except ValidationError:
        # old links dont have uuids
        raise Http404
    if request.method == "POST":
        form = ConsentForm(request.POST)
        if form.is_valid():
            # start anonymous experiment
            participant = None
            if form.cleaned_data["email_address"]:
                participant = Participant.objects.get_or_create(
                    team=request.team, email=form.cleaned_data["email_address"]
                )[0]
            channel = _ensure_channel_exists(experiment=experiment, platform="web", name=f"{experiment.id}-web")
            session = _start_experiment_session(
                experiment, get_real_user_or_none(request.user), participant=participant
            )
            ChannelSession.objects.get_or_create(
                experiment_channel=channel, experiment_session=session, external_chat_id=session.chat.id
            )
            return _record_consent_and_redirect(request, team_slug, session)

    else:
        form = ConsentForm(
            initial={
                "experiment_id": experiment.id,
            }
        )

    if experiment.consent_form:
        rendered_markdown = experiment.consent_form.consent_text
    else:
        consent_template = "experiments/consent/consent_default.md"
        rendered_markdown = render_to_string(consent_template, request=request)

    markdown_text = markdown.markdown(rendered_markdown)
    consent_notice = mark_safe(markdown_text)

    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "consent_notice": consent_notice,
            "form": form,
        },
    )


@team_admin_required
@user_passes_test(lambda u: u.is_superuser, login_url="/404")
def experiment_invitations(request, team_slug: str, experiment_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    sessions = experiment.sessions.order_by("-created_at").filter(
        status__in=["setup", "pending"],
        participant__isnull=False,
    )
    form = ExperimentInvitationForm(initial={"experiment_id": experiment.id})
    if request.method == "POST":
        post_form = ExperimentInvitationForm(request.POST)
        if post_form.is_valid():
            participant = Participant.objects.get_or_create(team=request.team, email=post_form.cleaned_data["email"])[0]
            if ExperimentSession.objects.filter(
                team=request.team,
                experiment=experiment,
                participant=participant,
                status__in=["setup", "pending"],
            ).exists():
                messages.info(request, "{} already has a pending invitation.".format(participant))
            else:
                session = ExperimentSession.objects.create(
                    team=request.team,
                    experiment=experiment,
                    llm=experiment.llm,
                    status="setup",
                    participant=participant,
                )
                if post_form.cleaned_data["invite_now"]:
                    send_experiment_invitation(session)
        else:
            form = post_form

    return TemplateResponse(
        request,
        "experiments/experiment_invitations.html",
        {
            "invitation_form": form,
            "experiment": experiment,
            "sessions": sessions,
        },
    )


@require_POST
@team_admin_required
@user_passes_test(lambda u: u.is_superuser, login_url="/404")
def download_experiment_chats(request, team_slug: str, experiment_id: str):
    # todo: this could be made more efficient and should be async, but just shipping something for now
    experiment = get_object_or_404(Experiment, id=experiment_id)

    # Create a HttpResponse with the CSV data and file attachment headers
    response = HttpResponse(experiment_to_csv(experiment).getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{experiment.name}-export.csv"'
    return response


def send_invitation(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    session = ExperimentSession.objects.get(experiment=experiment, public_id=session_id)
    send_experiment_invitation(session)
    return TemplateResponse(
        request,
        "experiments/manage/invite_row.html",
        context={"request": request, "experiment": experiment, "session": session},
    )


def _record_consent_and_redirect(request, team_slug: str, experiment_session: ExperimentSession):
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    experiment_session.user = get_real_user_or_none(request.user)
    if experiment_session.experiment.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirct_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirct_url_name = "experiments:experiment_chat"
    experiment_session.save()
    return HttpResponseRedirect(
        reverse(
            redirct_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.public_id],
        )
    )


@experiment_session_view(allowed_states=[SessionStatus.SETUP, SessionStatus.PENDING])
def start_experiment_session(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, public_id=experiment_id)
    experiment_session = get_object_or_404(ExperimentSession, experiment=experiment, public_id=session_id)

    if request.method == "POST":
        form = ConsentForm(request.POST)
        if form.is_valid():
            _check_and_process_seed_message(experiment_session)
            return _record_consent_and_redirect(request, team_slug, experiment_session)

    else:
        initial = {
            "experiment_id": experiment.id,
        }
        if experiment_session.participant:
            initial["participant_id"] = experiment_session.participant.id
            initial["email_address"] = experiment_session.participant.email
        elif not request.user.is_anonymous:
            initial["email_address"] = request.user.email
        form = ConsentForm(
            initial=initial,
        )

    consent_template = "experiments/consent/consent_default.md"
    rendered_markdown = render_to_string(consent_template, request=request)
    consent_notice = mark_safe(markdown.markdown(rendered_markdown))
    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "consent_notice": consent_notice,
            "form": form,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.PENDING_PRE_SURVEY])
def experiment_pre_survey(request, team_slug: str, experiment_id: str, session_id: str):
    if request.method == "POST":
        form = SurveyForm(request.POST)
        if form.is_valid():
            request.experiment_session.status = SessionStatus.ACTIVE
            request.experiment_session.save()
            return HttpResponseRedirect(
                reverse(
                    "experiments:experiment_chat",
                    args=[team_slug, experiment_id, session_id],
                )
            )
    else:
        form = SurveyForm()
    return TemplateResponse(
        request,
        "experiments/pre_survey.html",
        {
            "active_tab": "experiments",
            "form": form,
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
def experiment_chat(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": request.experiment,
            "session": request.experiment_session,
            "active_tab": "experiments",
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@require_POST
def end_experiment(request, team_slug: str, experiment_id: str, session_id: str):
    experiment_session = request.experiment_session
    experiment_session.ended_at = timezone.now()
    experiment_session.status = SessionStatus.PENDING_REVIEW
    experiment_session.save()
    return HttpResponseRedirect(reverse("experiments:experiment_review", args=[team_slug, experiment_id, session_id]))


@experiment_session_view(allowed_states=[SessionStatus.PENDING_REVIEW])
def experiment_review(request, team_slug: str, experiment_id: str, session_id: str):
    form = None
    if request.method == "POST":
        # no validation needed
        request.experiment_session.status = SessionStatus.COMPLETE
        request.experiment_session.reviewed_at = timezone.now()
        request.experiment_session.save()
        return HttpResponseRedirect(
            reverse("experiments:experiment_complete", args=[team_slug, experiment_id, session_id])
        )
    elif request.experiment.post_survey:
        form = SurveyForm()

    return TemplateResponse(
        request,
        "experiments/experiment_review.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
            "form": form,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.COMPLETE])
def experiment_complete(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_complete.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
        },
    )


@experiment_session_view()
def experiment_session_view(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_session_view.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
        },
    )
