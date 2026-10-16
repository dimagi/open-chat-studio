from django.db import transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from field_audit.models import AuditAction

from apps.ocs_notifications.notifications import moderation_provider_deleted_notification
from apps.service_providers.models import ModerationProvider
from apps.teams.models import Team


@receiver(pre_delete, sender=ModerationProvider)
def turn_moderation_off_when_selected_provider_is_deleted(sender, instance, **kwargs):
    """If the team had this provider selected, turn moderation off and notify the team after commit."""
    was_selected = Team.objects.filter(id=instance.team_id, moderation_provider_id=instance.id).update(
        moderation_enabled=False,
        moderation_deleted_provider_name=instance.name,
        audit_action=AuditAction.AUDIT,
    )
    if not was_selected:
        return

    team_id, provider_id, provider_name = instance.team_id, instance.id, instance.name

    def notify():
        # The team is gone when the provider was deleted as part of deleting the team.
        team = Team.objects.filter(id=team_id).first()
        if team:
            moderation_provider_deleted_notification(team=team, provider_id=provider_id, provider_name=provider_name)

    transaction.on_commit(notify)
