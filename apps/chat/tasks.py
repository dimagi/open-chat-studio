from uuid import UUID

from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import OuterRef, Subquery
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.experiments.models import ExperimentSession, SessionStatus
from apps.participants.models import Participant
from apps.web.meta import absolute_url

STATUSES_FOR_COMPLETE_CHATS = [SessionStatus.PENDING_REVIEW, SessionStatus.COMPLETE, SessionStatus.UNKNOWN]


def _get_latest_sessions_for_participants(
    participant_chat_ids: list, experiment_public_id: UUID
) -> list[ExperimentSession]:
    latest_session_id = (
        ExperimentSession.objects.filter(experiment__public_id=experiment_public_id, participant=OuterRef("pk"))
        .order_by("-created_at")
        .values("id")[:1]
    )

    latest_participant_session_ids = (
        Participant.objects.filter(
            experimentsession__experiment__public_id=experiment_public_id, identifier__in=participant_chat_ids
        )
        .annotate(latest_session_id=Subquery(latest_session_id))
        .values("latest_session_id")
    )

    return (
        ExperimentSession.objects.filter(id__in=Subquery(latest_participant_session_ids))
        .prefetch_related("experiment")
        .all()
    )


@shared_task(ignore_result=True)
def notify_users_of_safety_violations_task(experiment_session_id: int, safety_layer_id: int):
    experiment_session = ExperimentSession.objects.get(id=experiment_session_id)
    experiment = experiment_session.experiment
    if not experiment.safety_violation_notification_emails:
        return

    email_context = {
        "session_link": absolute_url(
            reverse(
                "chatbots:chatbot_session_view",
                kwargs={
                    "session_id": experiment_session.external_id,
                    "experiment_id": experiment.public_id,
                    "team_slug": experiment.team.slug,
                },
            )
        ),
        "safety_layer_link": absolute_url(
            reverse("experiments:safety_edit", kwargs={"pk": safety_layer_id, "team_slug": experiment.team.slug})
        ),
    }
    send_mail(
        subject=_("A Safety Layer was breached"),
        message=render_to_string("experiments/email/safety_violation.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=experiment.safety_violation_notification_emails,
        fail_silently=False,
        html_message=render_to_string("experiments/email/safety_violation.html", context=email_context),
    )
