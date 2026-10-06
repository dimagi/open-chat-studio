from allauth.account.signals import user_signed_up
from django.core.signals import request_finished
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from ..experiments.models import Experiment
from ..web.signals import migrate_finished
from .backends import create_default_groups
from .export.selection import clear_migrating_chatbot_count
from .helpers import create_default_team_for_user
from .invitations import get_invitation_from_request, process_invitation
from .models import Team
from .utils import unset_current_team


@receiver(user_signed_up)
def add_user_to_team(request, user, **kwargs):
    """
    Adds the user to the team if there is invitation information in the URL.
    """
    invitation = get_invitation_from_request(request)
    if invitation:
        process_invitation(invitation, user)
    elif not user.teams.exists():
        # if the sign up was from a social account, there may not be a default team, so create one
        create_default_team_for_user(user)


@receiver(m2m_changed, sender=Team.exportable_experiments.through)
def clear_count_on_allowlist_change(sender, instance, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        clear_migrating_chatbot_count(instance.pk)


@receiver(post_delete, sender=Experiment)
def clear_count_on_chatbot_delete(sender, instance, **kwargs):
    """Deleting a chatbot cascades to its allowlist rows, and Django sends no signal for rows of an
    auto-created through model."""
    clear_migrating_chatbot_count(instance.team_id)


@receiver(post_save, sender=Team)
def clear_count_when_migration_ends(sender, instance, **kwargs):
    if not instance.is_migrating:
        clear_migrating_chatbot_count(instance.pk)


@request_finished.connect
def clear_team_context(sender, **kwargs):
    unset_current_team()


_groups_created = []


@migrate_finished.connect
def sync_groups(sender, **kwargs):
    """
    Syncs the groups with the permissions.
    """
    create_groups_after_migrate()


def create_groups_after_migrate():
    """Use a separate function since you can't call signal handlers directly."""
    if not _groups_created:
        # all the apps we care about have been migrated
        print("Creating groups")
        create_default_groups()

    _groups_created.append(1)
