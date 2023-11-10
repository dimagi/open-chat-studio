from allauth.account.signals import user_signed_up
from django.core.signals import request_finished
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .backends import CONTENT_TYPES, create_default_groups
from .helpers import create_default_team_for_user
from .invitations import get_invitation_id_from_request, process_invitation
from .models import Invitation
from .utils import unset_current_team


@receiver(user_signed_up)
def add_user_to_team(request, user, **kwargs):
    """
    Adds the user to the team if there is invitation information in the URL.
    """
    invitation_id = get_invitation_id_from_request(request)
    if invitation_id:
        try:
            invitation = Invitation.objects.get(id=invitation_id)
            process_invitation(invitation, user)
        except Invitation.DoesNotExist:
            # for now just swallow missing invitation errors
            # these should get picked up by the form validation
            pass
    elif not user.teams.exists():
        # if the sign up was from a social account, there may not be a default team, so create one
        create_default_team_for_user(user)


@request_finished.connect
def clear_team_context(sender, **kwargs):
    unset_current_team()


apps_to_migrate = set(CONTENT_TYPES)


@post_migrate.connect
def sync_groups(sender, **kwargs):
    """
    Syncs the groups with the permissions.

    This signal is called after each app's migrations have completed (regardless of whether there
    were migrations run for that app).

    Since permissions are also created using a `post_migrate` signal we have to wait until all the
    permissions are created before we create the groups.
    """

    try:
        apps_to_migrate.remove(sender.label)
    except KeyError:
        pass

    if not apps_to_migrate:
        # all the apps we care about have been migrated
        print("Creating groups")
        create_default_groups()
