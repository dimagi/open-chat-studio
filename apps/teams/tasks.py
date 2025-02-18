from celery import shared_task

from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation, Team
from apps.teams.utils import current_team
from apps.utils.deletion import (
    chunk_list,
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
    admin_emails = get_admin_emails_with_delete_permission(team)
    team_name = team.name
    chunk_size = 50
    chunked_emails = chunk_list(admin_emails, chunk_size)
    delete_object_with_auditing_of_related_objects(team)
    for chunk_emails in chunked_emails:
        with current_team(team):
            send_team_deleted_notification(team_name, chunk_emails)
