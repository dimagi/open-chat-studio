from datetime import datetime, timedelta

import jwt
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.experiments.models import ExperimentSession, SessionStatus
from apps.web.meta import absolute_url


def send_experiment_invitation(experiment_session: ExperimentSession):
    if not experiment_session.participant:
        raise Exception("Session has no participant!")

    experiment_version_name = experiment_session.experiment_version.name
    email_context = {
        "session": experiment_session,
        "experiment_name": experiment_version_name,
    }
    send_mail(
        subject=_("You're invited to {}!").format(experiment_version_name),
        message=render_to_string("experiments/email/invitation.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[experiment_session.participant.email],
        fail_silently=False,
        html_message=render_to_string("experiments/email/invitation.html", context=email_context),
    )
    if experiment_session.status == SessionStatus.SETUP:
        experiment_session.status = SessionStatus.PENDING
        experiment_session.save()


def send_chat_link_email(experiment_session: ExperimentSession) -> datetime:
    expiry_time = timezone.now() + timedelta(minutes=settings.PUBLIC_CHAT_LINK_MAX_AGE)
    token = jwt.encode(
        {
            "exp": expiry_time,
            "session": str(experiment_session.external_id),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    path = reverse(
        "experiments:verify_public_chat_token",
        kwargs={
            "team_slug": experiment_session.team.slug,
            "experiment_id": experiment_session.experiment.public_id,
            "token": token,
        },
    )
    email_context = {
        "verify_link": absolute_url(relative_url=path),
        "experiment_name": experiment_session.experiment_version.name,
    }
    template = "experiments/email/verify_public_chat_email"
    send_mail(
        subject=_("Verify your email"),
        message=render_to_string(f"{template}.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[experiment_session.participant.identifier],
        fail_silently=False,
        html_message=render_to_string(f"{template}.html", context=email_context),
    )
    return expiry_time
