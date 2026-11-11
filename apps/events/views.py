from django.contrib.auth.decorators import permission_required
from django.db import models
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.chatbots.breadcrumbs import chatbot_crumbs
from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    ScheduledTriggerForm,
    StaticTriggerForm,
    TimeoutTriggerForm,
    build_action_params_form,
)
from apps.events.models import (
    ScheduledTrigger,
    StaticTrigger,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.events.services import TriggerForms
from apps.experiments.models import Experiment
from apps.generics.breadcrumbs import Crumb
from apps.teams.decorators import login_and_team_required


def _assert_team_experiment(request, experiment_id):
    """Raise 404 if experiment_id does not belong to request.team, treating cross-team ids as not found."""
    get_object_or_404(Experiment, id=experiment_id, team=request.team)


def _get_events_url(team_slug, experiment_id):
    url = reverse("chatbots:single_chatbot_home", args=[team_slug, experiment_id])
    return f"{url}#events"


def _event_breadcrumbs(request, experiment_id, leaf: str) -> list[Crumb]:
    experiment = get_object_or_404(Experiment.objects.get_all(), id=experiment_id, team=request.team)
    return [*chatbot_crumbs(request.team.slug, experiment), (leaf, None)]


@login_and_team_required
@permission_required("events.add_timeouttrigger")
def create_timeout_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(TimeoutTriggerForm, request, team_slug, experiment_id)


@login_and_team_required
@permission_required("events.add_statictrigger")
def create_static_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(StaticTriggerForm, request, team_slug, experiment_id)


@login_and_team_required
@permission_required("events.add_scheduledtrigger")
def create_scheduled_event_view(request, team_slug: str, experiment_id: str):
    return _create_event_view(ScheduledTriggerForm, request, team_slug, experiment_id)


def _create_event_view(trigger_form_class, request, team_slug: str, experiment_id: str):
    _assert_team_experiment(request, experiment_id)
    return _manage_event_view(trigger_form_class, request, team_slug, experiment_id, title=_("New Event"))


def _manage_event_view(trigger_form_class, request, team_slug: str, experiment_id: str, *, title: str, trigger=None):
    """Render the event form, or save the posted event and redirect to the events list."""
    trigger_forms = TriggerForms.bind(
        trigger_form_class,
        data=request.POST if request.method == "POST" else None,
        team_id=request.team.id,
        experiment_id=experiment_id,
        trigger=trigger,
    )
    if request.method == "POST" and trigger_forms.is_valid():
        trigger_forms.save()
        return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))

    context = _event_form_context(trigger_forms, trigger_form_class, experiment_id, request, title=title)
    return render(request, "events/manage_event.html", context)


def _event_form_context(trigger_forms: TriggerForms, trigger_form_class, experiment_id, request, title: str):
    namespace = request.resolver_match.namespace  # e.g., "chatbots:events"
    return {
        "title": title,
        "breadcrumbs": _event_breadcrumbs(request, experiment_id, title),
        "trigger_form": trigger_forms.trigger_form,
        "action_primary_form": trigger_forms.action_form,
        "action_params_form": trigger_forms.params_form,
        "action_type": trigger_forms.action_type,
        "event_type": trigger_form_class._meta.model._meta.model_name,
        "experiment_id": experiment_id,
        "end_trigger_types": StaticTriggerType.end_conversation_types(),
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


@login_and_team_required
@permission_required("events.change_scheduledtrigger")
def edit_scheduled_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _edit_event_view("scheduled", request, team_slug, experiment_id, trigger_id)


def _edit_event_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    trigger_form_class = {
        "static": StaticTriggerForm,
        "timeout": TimeoutTriggerForm,
        "scheduled": ScheduledTriggerForm,
    }[trigger_type]
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
        "scheduled": ScheduledTrigger,
    }[trigger_type]
    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team)

    return _manage_event_view(
        trigger_form_class, request, team_slug, experiment_id, title=_("Edit Event"), trigger=trigger
    )


@login_and_team_required
@permission_required("events.delete_statictrigger")
@require_POST
def delete_static_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("static", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.delete_timeouttrigger")
@require_POST
def delete_timeout_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("timeout", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.delete_scheduledtrigger")
@require_POST
def delete_scheduled_event_view(request, team_slug: str, experiment_id: str, trigger_id):
    return _delete_event_view("scheduled", request, team_slug, experiment_id, trigger_id)


def _delete_event_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
        "scheduled": ScheduledTrigger,
    }[trigger_type]
    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team)
    trigger.archive()
    return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))


@login_and_team_required
@permission_required("events.view_eventlog")
def static_logs_view(request, team_slug, experiment_id, trigger_id):
    trigger = get_object_or_404(
        StaticTrigger, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team
    )
    context = _get_event_logs_context(request, trigger)
    return render(request, "events/view_logs.html", context)


@login_and_team_required
@permission_required("events.view_eventlog")
def timeout_logs_view(request, team_slug, experiment_id, trigger_id):
    trigger = get_object_or_404(
        TimeoutTrigger, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team
    )
    context = _get_event_logs_context(request, trigger)
    return render(request, "events/view_logs.html", context)


@login_and_team_required
@permission_required("events.view_eventlog")
def scheduled_logs_view(request, team_slug: str, experiment_id: str, trigger_id):
    trigger = get_object_or_404(
        ScheduledTrigger, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team
    )
    context = _get_event_logs_context(request, trigger)
    return render(request, "events/view_logs.html", context)
    return render(request, "events/view_logs.html", context)


def _get_event_logs_context(request, trigger):
    title = _("Event Logs")
    return {
        "event_logs": trigger.event_logs.order_by("-created_at").all(),
        "title": title,
        "breadcrumbs": _event_breadcrumbs(request, trigger.experiment_id, title),
        "trigger": trigger,
    }


@login_and_team_required
@permission_required("events.change_statictrigger")
@require_POST
def toggle_static_active_status(request, team_slug: str, experiment_id: str, trigger_id):
    return _toggle_event_status_view("static", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.change_timeouttrigger")
@require_POST
def toggle_timeout_active_status(request, team_slug: str, experiment_id: str, trigger_id):
    return _toggle_event_status_view("timeout", request, team_slug, experiment_id, trigger_id)


@login_and_team_required
@permission_required("events.change_scheduledtrigger")
@require_POST
def toggle_scheduled_active_status(request, team_slug: str, experiment_id: str, trigger_id):
    return _toggle_event_status_view("scheduled", request, team_slug, experiment_id, trigger_id)


def _toggle_event_status_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
        "scheduled": ScheduledTrigger,
    }[trigger_type]

    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id, experiment__team=request.team)
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
