from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.channels.utils import delete_experiment_session_cached
from apps.experiments.models import ExperimentSession


@receiver(pre_save, sender=ExperimentSession)
def invalidate_widget_session_cache(sender, instance: ExperimentSession, **kwargs):
    """Invalidate widget session cache when session is updated."""
    if instance.external_id:
        delete_experiment_session_cached(instance.external_id)
