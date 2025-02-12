from celery import shared_task

from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation, Team
from apps.utils.deletion import (
    delete_object_with_auditing_of_related_objects,
    get_admin_emails_with_delete_permission,
    send_team_deleted_notification,
)


@shared_task(ignore_result=True)
def send_invitation_accepted_notification(invitation_id):
    invitation = Invitation.objects.get(id=invitation_id)
    send_invitation_accepted(invitation)


@shared_task
def delete_team_async(team_id):
    team = Team.objects.get(id=team_id)

    # get required info for emails prior to deleting team info
    team_name = team.name
    admin_emails = get_admin_emails_with_delete_permission(team)
    delete_object_with_auditing_of_related_objects(team)
    send_team_deleted_notification(team_name, admin_emails)
