import hashlib

from django.contrib.auth.models import AbstractUser, UserManager
from django.core.cache import cache
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.users.model_audit_fields import CUSTOM_USER_FIELDS
from apps.web.storage_backends import get_public_media_storage


class AuditedUserObjectManager(UserManager, AuditingManager):
    # Disable this to work around account.0006_emailaddress_lower migration
    # which does a data migration using the user model but does not pass the `audit_action` keyword.
    use_in_migrations = False


@audit_fields(*CUSTOM_USER_FIELDS, audit_special_queryset_writes=True)
class CustomUser(AbstractUser):
    """
    Add additional fields to the user model here.
    """

    objects = AuditedUserObjectManager()
    migration_objects = UserManager()
    avatar = models.FileField(upload_to="profile-pictures/", blank=True, storage=get_public_media_storage)
    language = models.CharField(max_length=10, blank=True, null=True)  # noqa DJ001

    class Meta:
        # Set the base manager to the default UserManager to avoid issues with the auditing queryset in
        # migrations etc e.g. 3rd party apps that do data migrations won't include the audit_action keyword.
        base_manager_name = "migration_objects"

    def __str__(self):
        return f"{self.get_full_name()} <{self.email or self.username}>"

    def get_display_name(self) -> str:
        if self.get_full_name().strip():
            return self.get_full_name()
        return self.email or self.username

    @property
    def avatar_url(self) -> str:
        if self.avatar:
            return self.avatar.url
        else:
            return f"https://www.gravatar.com/avatar/{self.gravatar_id}?s=128&d=identicon"

    @property
    def gravatar_id(self) -> str:
        # https://en.gravatar.com/site/implement/hash/
        return hashlib.md5(self.email.lower().strip().encode("utf-8")).hexdigest()

    def unread_notifications_count(self) -> int:
        cache_key = f"{self.id}-unread-notifications-count"
        if count := cache.get(cache_key):
            return count

        count = self.notifications.through.objects.filter(read=False).count()
        cache.set(cache_key, count, 5 * 3600)
        return count
