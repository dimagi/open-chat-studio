from celery import shared_task

from apps.teams.invitations import send_invitation_accepted
from apps.teams.models import Invitation, Membership, Team
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
def delete_team_async(team_id, user_email, notify_recipients="self"):
    team = Team.objects.get(id=team_id)
    emails = [user_email]  # default case: user sends email just to themselves
    if notify_recipients == "admins":
        emails = get_admin_emails_with_delete_permission(team)
    elif notify_recipients == "all":
        emails = list(Membership.objects.filter(team__name=team.name).values_list("user__email", flat=True))
    team_name = team.name
    chunk_size = 50
    chunked_emails = chunk_list(emails, chunk_size)
    with current_team(team):
        delete_object_with_auditing_of_related_objects(team)
        for chunk_emails in chunked_emails:
            send_team_deleted_notification(team_name, chunk_emails)
