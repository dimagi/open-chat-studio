import hashlib

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.users.model_audit_fields import CUSTOM_USER_FIELDS
from apps.web.storage_backends import get_public_media_storage


class AuditedUserObjectManager(UserManager, AuditingManager):
    pass


@audit_fields(*CUSTOM_USER_FIELDS, audit_special_queryset_writes=True)
class CustomUser(AbstractUser):
    """
    Add additional fields to the user model here.
    """

    objects = AuditedUserObjectManager()
    avatar = models.FileField(upload_to="profile-pictures/", blank=True, storage=get_public_media_storage)
    language = models.CharField(max_length=10, blank=True, null=True)  # noqa DJ001

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
