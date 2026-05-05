from django.contrib.auth.decorators import permission_required
from django.db import models
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    EventActionForm,
    StaticTriggerForm,
    TimeoutTriggerForm,
    build_action_params_form,
)
from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.teams.decorators import login_and_team_required


def _get_events_url(team_slug, experiment_id):
    url = reverse("chatbots:single_chatbot_home", args=[team_slug, experiment_id])
    return f"{url}#events"


@login_and_team_required
@permission_required("events.add_timeouttrigger")
def create_timeout_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(TimeoutTriggerForm, request, team_slug, experiment_id)


@login_and_team_required
@permission_required("events.add_statictrigger")
def create_static_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(StaticTriggerForm, request, team_slug, experiment_id)


def _create_event_view(trigger_form_class, request, team_slug: str, experiment_id: str):
    if request.method == "POST":
        action_type = request.POST.get("action_type") or _default_action_type()
        action_primary_form = EventActionForm(request.POST)
        action_params_form = build_action_params_form(
            action_type,
            data=request.POST,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(request.POST)

        if action_primary_form.is_valid() and action_params_form.is_valid() and trigger_form.is_valid():
            saved_action = action_primary_form.save(experiment_id=experiment_id)
            saved_action.params = action_params_form.cleaned_data
            saved_action.save()
            trigger = trigger_form.save(commit=False, experiment_id=experiment_id)
            trigger.action = saved_action
            trigger.save()
            return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))
    else:
        action_type = _default_action_type()
        action_primary_form = EventActionForm()
        action_params_form = build_action_params_form(
            action_type,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class()

    context = _event_form_context(
        trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class, experiment_id, request
    )
    return render(request, "events/manage_event.html", context)


def _default_action_type() -> str:
    """First key in ACTION_PARAMS_FORMS."""
    return next(iter(ACTION_PARAMS_FORMS))


def _event_form_context(
    trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class, experiment_id, request
):
    namespace = request.resolver_match.namespace  # e.g., "chatbots:events"
    return {
        "trigger_form": trigger_form,
        "action_primary_form": action_primary_form,
        "action_params_form": action_params_form,
        "action_type": action_type,
        "event_type": trigger_form_class._meta.model._meta.model_name,
        "experiment_id": experiment_id,
        "action_params_url": reverse(
            f"{namespace}:action_params_form",
            args=[request.team.slug, experiment_id],
        ),
    }


@login_and_team_required
@permission_required("events.change_statictrigger")
def edit_static_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _edit_event_view("static", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.change_timeouttrigger")
def edit_timeout_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _edit_event_view("timeout", request, team_slug, experiment_id, trigger_id)


def _edit_event_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    trigger_form_class = {
        "static": StaticTriggerForm,
        "timeout": TimeoutTriggerForm,
    }[trigger_type]
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
    }[trigger_type]
    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id)

    if request.method == "POST":
        action_type = request.POST.get("action_type") or trigger.action.action_type
        action_primary_form = EventActionForm(request.POST, instance=trigger.action)
        action_params_form = build_action_params_form(
            action_type,
            data=request.POST,
            initial=trigger.action.params,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(request.POST, instance=trigger)
        if action_primary_form.is_valid() and action_params_form.is_valid() and trigger_form.is_valid():
            saved_action = action_primary_form.save(experiment_id=experiment_id)
            saved_action.params = action_params_form.cleaned_data
            saved_action.save()
            trigger_form.save(experiment_id=experiment_id)
            return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))
    else:
        action_type = trigger.action.action_type
        action_primary_form = EventActionForm(instance=trigger.action)
        action_params_form = build_action_params_form(
            action_type,
            initial=trigger.action.params,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(instance=trigger)

    context = _event_form_context(
        trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class, experiment_id, request
    )
    return render(request, "events/manage_event.html", context)


@login_and_team_required
@permission_required("events.delete_statictrigger")
def delete_static_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("static", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.delete_timeouttrigger")
def delete_timeout_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("timeout", request, team_slug, experiment_id, trigger_id)


def _delete_event_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
    }[trigger_type]
    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id)
    trigger.archive()
    return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))


@login_and_team_required
@permission_required("events.view_eventlog")
def static_logs_view(request, team_slug, experiment_id, trigger_id):
    trigger = get_object_or_404(StaticTrigger, id=trigger_id, experiment_id=experiment_id)
    context = _get_event_logs_context(trigger)
    return render(request, "events/view_logs.html", context)


@login_and_team_required
@permission_required("events.view_eventlog")
def timeout_logs_view(request, team_slug, experiment_id, trigger_id):
    trigger = get_object_or_404(TimeoutTrigger, id=trigger_id, experiment_id=experiment_id)
    context = _get_event_logs_context(trigger)
    return render(request, "events/view_logs.html", context)


def _get_event_logs_context(trigger):
    return {
        "event_logs": trigger.event_logs.order_by("-created_at").all(),
        "title": "Event logs",
        "trigger": trigger,
    }


@login_and_team_required
@permission_required("events.change_statictrigger")
def toggle_static_active_status(request, team_slug: str, experiment_id: str, trigger_id):
    return _toggle_event_status_view("static", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.change_timeouttrigger")
def toggle_timeout_active_status(request, team_slug: str, experiment_id: str, trigger_id):
    return _toggle_event_status_view("timeout", request, team_slug, experiment_id, trigger_id)


def _toggle_event_status_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
    }[trigger_type]

    trigger = get_object_or_404(model_class, id=trigger_id)
    working_root = trigger.get_working_version()
    all_versions = model_class.objects.filter(models.Q(id=working_root.id) | models.Q(working_version=working_root))
    new_status = not trigger.is_active
    all_versions.update(is_active=new_status)

    return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))


@login_and_team_required
def action_params_form_view(request, team_slug: str, experiment_id: str):
    """Return the action-params secondary form fragment for ``action_type``.

    Reachable from the same pages that already require event create/change perms.
    """
    action_type = request.GET.get("action_type")
    if action_type not in ACTION_PARAMS_FORMS:
        return HttpResponseBadRequest("Invalid action_type")
    form = build_action_params_form(
        action_type,
        team_id=request.team.id,
        experiment_id=experiment_id,
    )
    return render(request, "events/_action_params_form.html", {"form": form})
