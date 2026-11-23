from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.annotations.models import CustomTaggedItem, Tag
from apps.chat.models import Chat
from apps.teams.models import Team

from ..teams.utils import current_team
from .const import DEFAULT_CONSENT_TEXT
from .models import ConsentForm, ExperimentSession
from .services import touch_rows_tagged_with, touch_tagged_objects


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


@receiver(post_save, sender=CustomTaggedItem)
def touch_tagged_sync_rows(sender, instance, **kwargs):
    """A tag change moves its message's or session's ``updated_at``, so the v2 sync API sends it again."""
    touch_tagged_objects(instance.content_type_id, [instance.object_id])


# Tag rows are removed through ``remove_tags``, which runs taggit's ``remove()`` and this receiver in one
# transaction, or through ``delete_tagged_items``, which touches the rows itself and keeps the delete set-based.
@receiver(m2m_changed, sender=CustomTaggedItem)
def touch_untagged_sync_row(sender, instance, action, pk_set, **kwargs):
    if action == "post_clear" or (action == "post_remove" and pk_set):
        touch_tagged_objects(ContentType.objects.get_for_model(instance).id, [instance.pk])


@receiver(pre_save, sender=Tag)
def remember_tag_name(sender, instance, **kwargs):
    saved = Tag.objects.filter(pk=instance.pk).values_list("name", flat=True) if instance.pk else Tag.objects.none()
    instance._saved_name = saved.first()


@receiver(post_save, sender=Tag)
def touch_rows_of_renamed_tag(sender, instance, created, **kwargs):
    """A renamed tag changes the ``tags`` the v2 sync API returns for every row that carries it."""
    if not created and getattr(instance, "_saved_name", None) not in (None, instance.name):
        touch_rows_tagged_with(instance)


@receiver(pre_delete, sender=Tag)
def touch_rows_of_deleted_tag(sender, instance, **kwargs):
    """Deleting a tag removes it from the ``tags`` the v2 sync API returns for every row that carried it."""
    touch_rows_tagged_with(instance)
