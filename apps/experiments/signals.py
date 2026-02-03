from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.teams.models import Team

from ..teams.utils import current_team
from .const import DEFAULT_CONSENT_TEXT
from .models import ConsentForm


@receiver(post_save, sender=Team)
def create_default_consent_for_team_handler(sender, instance, created, **kwargs):
    if created:
        create_default_consent_for_team(instance)


def create_default_consent_for_team(team):
    with current_team(team):
        ConsentForm.objects.get_or_create(
            team=team,
            is_default=True,
            defaults={
                "name": "Default Consent",
                "consent_text": DEFAULT_CONSENT_TEXT,
            },
        )
