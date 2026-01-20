from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.channels.utils import delete_experiment_session_cached
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession


@receiver(pre_save, sender=ExperimentSession)
def invalidate_widget_session_cache(sender, instance: ExperimentSession, **kwargs):
    """Invalidate widget session cache when session is updated."""
    if instance.external_id:
        delete_experiment_session_cached(instance.external_id)


@receiver(post_save, sender=ChatMessage)
def update_session_last_activity(sender, instance: ChatMessage, created, **kwargs):
    """Update ExperimentSession.last_activity_at when a new chat message is created."""
    if not created:
        return

    try:
        experiment_session = instance.chat.experiment_session
        experiment_session.last_activity_at = timezone.now()
        update_fields = ["last_activity_at"]
        if not experiment_session.first_activity_at:
            experiment_session.first_activity_at = timezone.now()
            update_fields.append("first_activity_at")

        experiment_session.save(update_fields=update_fields)
    except ExperimentSession.DoesNotExist:
        # No experiment session associated with this chat
        pass
