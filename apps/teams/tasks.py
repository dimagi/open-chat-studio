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
def delete_team_async(team_id, notify_recipients="self"):
    team = Team.objects.get(id=team_id)
    # if notify_recipients == "self":
    # Send email only to the user who initiated the action
    # elif notify_recipients == "admins":
    # Send email to all admins
    # elif notify_recipients == "all":
    # Get all member emails and send email to all members of the domain

    admin_emails = get_admin_emails_with_delete_permission(team)
    team_name = team.name
    chunk_size = 50
    chunked_emails = chunk_list(admin_emails, chunk_size)
    with current_team(team):
        delete_object_with_auditing_of_related_objects(team)
        for chunk_emails in chunked_emails:
            send_team_deleted_notification(team_name, chunk_emails)
