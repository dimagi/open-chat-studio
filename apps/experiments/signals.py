from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem
from apps.chat.models import Chat, ChatMessage
from apps.teams.models import Team

from ..teams.utils import current_team
from .const import DEFAULT_CONSENT_TEXT
from .models import ConsentForm, ExperimentSession


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


@receiver(post_delete, sender=ExperimentSession)
def delete_chat_on_session_delete(sender, instance, **kwargs):
    if instance.chat_id:
        Chat.objects.filter(id=instance.chat_id).delete()


@receiver([post_save, post_delete], sender=CustomTaggedItem)
def touch_tagged_sync_rows(sender, instance, **kwargs):
    """A tag change moves its message's or session's ``updated_at``, so the v2 sync API sends it again.

    Tags are rows of their own, so the tagged message or session is touched here.
    """
    content_type_id = instance.content_type_id
    if content_type_id == ContentType.objects.get_for_model(ChatMessage).id:
        ChatMessage.objects.filter(pk=instance.object_id).update(updated_at=timezone.now())
    elif content_type_id == ContentType.objects.get_for_model(Chat).id:
        ExperimentSession.objects.filter(chat_id=instance.object_id).update(updated_at=timezone.now())
