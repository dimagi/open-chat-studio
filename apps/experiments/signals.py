from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.annotations.models import CustomTaggedItem, Tag
from apps.chat.models import Chat
from apps.teams.models import Team

from ..teams.utils import current_team
from .const import DEFAULT_CONSENT_TEXT
from .models import ConsentForm, ExperimentSession
from .services import touch_rows_tagged_with, touch_tagged_object


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
    """A tag change moves its message's or session's ``updated_at``, so the v2 sync API sends it again."""
    touch_tagged_object(instance.content_type_id, instance.object_id)


@receiver(pre_save, sender=Tag)
def remember_tag_name(sender, instance, **kwargs):
    saved = Tag.objects.filter(pk=instance.pk).values_list("name", flat=True) if instance.pk else Tag.objects.none()
    instance._saved_name = saved.first()


@receiver(post_save, sender=Tag)
def touch_rows_of_renamed_tag(sender, instance, created, **kwargs):
    """A renamed tag changes the ``tags`` the v2 sync API returns for every row that carries it."""
    if not created and getattr(instance, "_saved_name", None) not in (None, instance.name):
        touch_rows_tagged_with(instance)
