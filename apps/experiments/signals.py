from django.core.exceptions import ProtectedError
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.teams.models import Team

from .const import DEFAULT_CONSENT_TEXT
from .models import ConsentForm


@receiver(post_save, sender=Team)
def create_default_consent_for_team_handler(sender, instance, created, **kwargs):
    if created:
        create_default_consent_for_team(instance)


def create_default_consent_for_team(team):
    ConsentForm.objects.get_or_create(
        team=team,
        is_default=True,
        defaults={
            "name": "Default Consent",
            "consent_text": DEFAULT_CONSENT_TEXT,
        },
    )


@receiver(pre_delete, sender="service_providers.LlmProvider")
@receiver(pre_delete, sender="assistants.OpenAiAssistant")
@receiver(pre_delete, sender="SourceMaterial")
@receiver(pre_delete, sender="Survey")
@receiver(pre_delete, sender="ConsentForm")
@receiver(pre_delete, sender="service_providers.VoiceProvider")
@receiver(pre_delete, sender="SyntheticVoice")
def protect_referenced_objects(sender, instance, **kwargs):
    try:
        instance.delete()
    except ProtectedError:
        print(
            f"Cannot delete {instance}. It is still referenced by an ExperimentVersion. Please delete the references first."
        )
        raise
