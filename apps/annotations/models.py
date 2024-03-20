from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from field_audit import audit_fields
from taggit.managers import TaggableManager
from taggit.models import GenericTaggedItemBase, TagBase

from apps.teams.models import BaseTeamModel, Team
from apps.users.models import CustomUser


@audit_fields(
    "name",
    "slug",
    "created_by",
    "team",
)
class Tag(TagBase, BaseTeamModel):
    name = models.CharField(verbose_name=pgettext_lazy("A tag name", "name"), max_length=100)
    created_by = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING)

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        unique_together = ("team", "name")
        ordering = ["name"]


@audit_fields(
    "user",
    "team",
    "tag",
    "object_id",
    "content_type",
)
class CustomTaggedItem(GenericTaggedItemBase, BaseTeamModel):
    user = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING)
    tag = models.ForeignKey(Tag, related_name="%(app_label)s_%(class)s_items", on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
            )
        ]

        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "object_id", "tag"),
                name="content_type_object_id_tag_id_4bb97a8e_uniq",
            )
        ]


class BaseTaggedModel(models.Model):
    class Meta:
        abstract = True

    tags = TaggableManager(through=CustomTaggedItem)

    def add_tags(self, tags: list[str], team: Team, added_by: CustomUser):
        self.tags.add(tags, through_defaults={"team": team, "user": added_by})

    @property
    def object_info(self):
        import json

        return json.dumps(
            {
                "id": self.id,
                "app": self._meta.app_label,
                "model_name": self._meta.model_name,
            }
        )

    @property
    def get_linked_tags(self):
        # return [{"user": item.user.username, "tag": item.tag.name} for item in self.tagged_items.all()]
        return [item.tag.name for item in self.tagged_items.all()]
