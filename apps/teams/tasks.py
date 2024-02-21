from celery import shared_task

from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation


@shared_task
def send_invitation_accepted_notification(invitation_id):
    invitation = Invitation.objects.get(id=invitation_id)
    send_invitation_accepted(invitation)
