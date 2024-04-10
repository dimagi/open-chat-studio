from django.contrib.auth.decorators import permission_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.events.forms import (
    StaticTriggerForm,
    TimeoutTriggerForm,
    get_action_params_form,
)
from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.teams.decorators import login_and_team_required


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
        action_form = get_action_params_form(request.POST)
        trigger_form = trigger_form_class(request.POST)
        if action_form.is_valid() and trigger_form.is_valid():
            saved_action = action_form.save(experiment_id=experiment_id)
            trigger = trigger_form.save(commit=False, experiment_id=experiment_id)
            trigger.action = saved_action
            trigger.save()
            return HttpResponseRedirect(reverse("experiments:single_experiment_home", args=[team_slug, experiment_id]))
    else:
        action_form = get_action_params_form()
        trigger_form = trigger_form_class()
    context = {
        "action_form": action_form,
        "trigger_form": trigger_form,
    }
    return render(request, "events/manage_event.html", context)


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
        action_form = get_action_params_form(request.POST, instance=trigger.action)
        trigger_form = trigger_form_class(request.POST, instance=trigger)

        if action_form.is_valid() and trigger_form.is_valid():
            action_form.save(experiment_id=experiment_id)
            trigger = trigger_form.save(experiment_id=experiment_id)
            return HttpResponseRedirect(reverse("experiments:single_experiment_home", args=[team_slug, experiment_id]))
    else:
        action_form = get_action_params_form(instance=trigger.action)
        trigger_form = trigger_form_class(instance=trigger)

    context = {
        "action_form": action_form,
        "trigger_form": trigger_form,
        "secondary_key": action_form.get_secondary_key(trigger.action),
    }
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
    trigger.delete()
    return HttpResponseRedirect(reverse("experiments:single_experiment_home", args=[team_slug, experiment_id]))


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
