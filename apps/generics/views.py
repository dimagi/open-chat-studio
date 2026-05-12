from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.translation import gettext
from waffle import flag_is_active

from apps.annotations.models import Tag
from apps.annotations.prefetch import chat_tagged_items_prefetch
from apps.events.models import StaticTrigger, StaticTriggerType
from apps.experiments.decorators import experiment_session_view
from apps.experiments.models import ExperimentSession
from apps.human_annotations.models import AnnotationItem
from apps.teams.flags import Flags


def render_session_details(
    request, team_slug, experiment_id, session_id, active_tab, template_path, session_type="Experiment"
):
    session = ExperimentSession.objects.prefetch_related(chat_tagged_items_prefetch()).get(
        external_id=session_id, team__slug=team_slug
    )
    experiment = request.experiment
    participant = session.participant
    annotation_queue_names = []
    if flag_is_active(request, Flags.HUMAN_ANNOTATIONS.slug):
        annotation_queue_names = list(
            AnnotationItem.objects.filter(session=session, queue__team=session.team).values_list(
                "queue__name", flat=True
            )
        )
    return TemplateResponse(
        request,
        template_path,
        {
            "experiment": experiment,
            "experiment_session": session,
            "active_tab": active_tab,
            "annotation_queue_names": annotation_queue_names,
            "details": [
                (gettext("Participant"), session.get_participant_chip()),
                (gettext("Remote ID"), participant.remote_id if participant and participant.remote_id else "-"),
                (gettext("Status"), session.get_status_display),
                (gettext("Started"), session.consent_date or session.created_at),
                (gettext("Ended"), session.ended_at or "-"),
                (gettext(session_type), experiment.name),
            ],
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False).all()],
            "event_triggers": [
                {
                    "event_logs": trigger.event_logs.filter(session=session).order_by("-created_at").all(),
                    "trigger": trigger,
                }
                for trigger in experiment.event_triggers
            ],
            "participant_schedules": session.participant.get_schedules_for_experiment(
                experiment.id, as_dict=True, include_inactive=True
            ),
            "participant_id": session.participant_id,
            "has_conversation_end_events": StaticTrigger.objects.filter(
                experiment=experiment, type__in=StaticTriggerType.end_conversation_types(), is_active=True
            ).exists(),
        },
    )


@experiment_session_view()
def paginate_session(request, team_slug, experiment_id, session_id, view_name):
    session = request.experiment_session
    experiment = request.experiment
    query = ExperimentSession.objects.exclude(external_id=session_id).filter(experiment=experiment)
    if request.GET.get("dir", "next") == "next":
        next_session = query.filter(created_at__gte=session.created_at).order_by("created_at").first()
    else:
        next_session = query.filter(created_at__lte=session.created_at).order_by("created_at").last()
    if not next_session:
        messages.warning(request, "No more sessions to paginate")
        return redirect(view_name, team_slug, experiment_id, session_id)
    return redirect(view_name, team_slug, experiment_id, next_session.external_id)
