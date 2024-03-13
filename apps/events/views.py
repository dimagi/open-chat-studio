from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.events.forms import EventActionForm, StaticTriggerForm, TimeoutTriggerForm
from apps.events.models import EventActionType, StaticTrigger, StaticTriggerType, TimeoutTrigger
from apps.utils.time import seconds_to_human


def create_timeout_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(TimeoutTriggerForm, request, team_slug, experiment_id)


def create_static_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(StaticTriggerForm, request, team_slug, experiment_id)


def _create_event_view(trigger_form_class, request, team_slug: str, experiment_id: str):
    if request.method == "POST":
        action_form = EventActionForm(request.POST)
        if action_form.is_valid():
            saved_action = action_form.save(experiment_id=experiment_id)
            trigger_form = trigger_form_class(request.POST)
            if trigger_form.is_valid():
                trigger = trigger_form.save(commit=False, experiment_id=experiment_id)
                trigger.action = saved_action
                trigger.save()
                return HttpResponseRedirect(
                    reverse("experiments:single_experiment_home", args=[team_slug, experiment_id])
                )
    else:
        action_form = EventActionForm()
        trigger_form = trigger_form_class()
    context = {
        "action_form": action_form,
        "trigger_form": trigger_form,
    }
    return render(request, "events/manage_event.html", context)


def edit_static_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _edit_event_view("static", request, team_slug, experiment_id, trigger_id)


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
        action_form = EventActionForm(request.POST, instance=trigger.action)
        trigger_form = trigger_form_class(request.POST, instance=trigger)

        if action_form.is_valid() and trigger_form.is_valid():
            action_form.save(experiment_id=experiment_id)
            trigger = trigger_form.save(experiment_id=experiment_id)
            return HttpResponseRedirect(reverse("experiments:single_experiment_home", args=[team_slug, experiment_id]))
    else:
        action_form = EventActionForm(instance=trigger.action)
        trigger_form = trigger_form_class(instance=trigger)

    context = {
        "action_form": action_form,
        "trigger_form": trigger_form,
    }
    return render(request, "events/manage_event.html", context)


def delete_static_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("static", request, team_slug, experiment_id, trigger_id)


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


def static_logs_view(request, team_slug, experiment_id, trigger_id):
    return _logs_view("static", request, team_slug, experiment_id, trigger_id)


def timeout_logs_view(request, team_slug, experiment_id, trigger_id):
    return _logs_view("timeout", request, team_slug, experiment_id, trigger_id)


def _logs_view(trigger_type, request, team_slug, experiment_id, trigger_id):
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
    }[trigger_type]

    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id)
    if trigger_type == "timeout":
        trigger_text = f"No response for {seconds_to_human(trigger.delay)}"
    else:
        trigger_text = StaticTriggerType(trigger.type).label

    context = {
        "trigger_text": trigger_text,
        "action_type": EventActionType(trigger.action.action_type).label,
        "event_logs": trigger.event_logs.order_by("-created_at").all(),
        "title": "Event logs",
        "trigger": trigger,
    }
    return render(request, "events/view_logs.html", context)
