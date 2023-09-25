from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from apps.experiments.models import ExperimentSession


def send_experiment_invitation(experiment_session: ExperimentSession):
    if not experiment_session.participant:
        raise Exception("Session has no participant!")

    email_context = {
        "session": experiment_session,
    }
    send_mail(
        subject=_("You're invited to {}!").format(experiment_session.experiment.name),
        message=render_to_string("experiments/email/invitation.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[experiment_session.participant.email],
        fail_silently=False,
        html_message=render_to_string("experiments/email/invitation.html", context=email_context),
    )
    if experiment_session.status == "setup":
        experiment_session.status = "pending"
        experiment_session.save()
