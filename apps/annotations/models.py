import json
import logging
from functools import cached_property
from typing import ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from field_audit import audit_fields
from taggit.managers import TaggableManager
from taggit.models import GenericTaggedItemBase, TagBase

from apps.teams.models import BaseTeamModel, Team
from apps.teams.utils import get_slug_for_team
from apps.users.models import CustomUser

logger = logging.getLogger("ocs.annotations")


class TagCategories(models.TextChoices):
    BOT_RESPONSE = "bot_response", _("Bot Response")
    SAFETY_LAYER_RESPONSE = "safety_layer_response", _("Safety Layer Response")
    EXPERIMENT_VERSION = "experiment_version", _("Experiment Version")
    RESPONSE_RATING = "response_rating", _("Response Rating")
    ERROR = "error", _("Error")
    VOICE = "voice", _("Voice")


@audit_fields(
    "name",
    "slug",
    "created_by",
    "team",
)
class Tag(TagBase, BaseTeamModel):
    name = models.CharField(verbose_name=pgettext_lazy("A tag name", "name"), max_length=100)
    created_by = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING, null=True, default=None)
    is_system_tag = models.BooleanField(default=False)
    category = models.CharField(choices=TagCategories.choices, blank=True, default="")

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        unique_together = ("team", "name", "is_system_tag", "category")
        ordering = ["name"]

    def get_absolute_url(self):
        return reverse("annotations:tag_edit", args=[get_slug_for_team(self.team_id), self.id])

    @property
    def label(self):
        return TagCategories(self.category).label if self.category else "User Defined"


@audit_fields(
    "user",
    "team",
    "tag",
    "object_id",
    "content_type",
)
class CustomTaggedItem(GenericTaggedItemBase, BaseTeamModel):
    user = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING, null=True, default=None)
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


class AnnotationMixin:
    @cached_property
    def object_info(self):
        return json.dumps(
            {
                "id": self.id,
                "app": self._meta.app_label,
                "model_name": self._meta.model_name,
            }
        )


class TaggedModelMixin(models.Model, AnnotationMixin):
    """Models supporting `tags` should use this mixin"""

    class Meta:
        abstract = True

    tags = TaggableManager(through=CustomTaggedItem)
    _skipped_category_tags: ClassVar[list] = [TagCategories.VOICE]  # Tag categories with special treatment

    def add_tags(self, tags: list[str], team: Team, added_by: CustomUser = None):
        tag_objs = Tag.objects.filter(team=team, name__in=tags)
        for tag in tag_objs:
            self.add_tag(tag, team, added_by)

    def create_and_add_tag(self, tag: str, team: Team, tag_category: TagCategories, added_by: CustomUser = None):
        tag, _ = Tag.objects.get_or_create(
            name=tag,
            team=team,
            is_system_tag=bool(tag_category),
            category=tag_category or "",
        )
        self.add_tag(tag, team=team, added_by=added_by)

    def add_tag(self, tag: Tag, team: Team, added_by: CustomUser = None):
        self.tags.add(tag, through_defaults={"team": team, "user": added_by})

    def user_tag_names(self):
        return {tag["name"] for tag in self.prefetched_tags_json if not tag["is_system_tag"]}

    def system_tags_names(self):
        return {(tag["name"], tag["category"]) for tag in self.prefetched_tags_json if tag["is_system_tag"]}

    def all_tag_names(self):
        return [tag["name"] for tag in self.prefetched_tags_json]

    def has_voice_tag(self):
        return any([tag for tag in self.prefetched_tags_json if tag["category"] == TagCategories.VOICE])

    def non_skipped_tags(self):
        return [tag for tag in self.prefetched_tags_json if tag["category"] not in self._skipped_category_tags]

    @cached_property
    def prefetched_tags_json(self):
        if not hasattr(self, "prefetched_tagged_items"):
            logger.warning("Warning: unable to prefectch tags")
            return []

        tags = []
        for tagged_item in self.prefetched_tagged_items:
            if tagged_item.tag.is_system_tag:
                added_by = "System"
            elif tagged_item.user and tagged_item.user.email:
                added_by = tagged_item.user.email
            else:
                added_by = "Participant"
            tags.append(
                {
                    "name": tagged_item.tag.name,
                    "id": tagged_item.tag.id,
                    "is_system_tag": tagged_item.tag.is_system_tag,
                    "category": tagged_item.tag.category,
                    "added_by": added_by,
                }
            )
        return tags


class UserComment(BaseTeamModel):
    user = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING)
    comment = models.TextField(blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    @transaction.atomic()
    @staticmethod
    def add_for_model(model, comment: str, added_by: CustomUser, team: Team) -> "UserComment":
        if model._meta.get_field("comments"):
            UserComment.objects.create(content_object=model, user=added_by, comment=comment, team=team)

    def __str__(self):
        return f'<{self.user.username}>: "{self.comment}"'


class UserCommentsMixin(models.Model, AnnotationMixin):
    comments = GenericRelation(UserComment)

    class Meta:
        abstract = True
        ordering = ["created_at"]

    def get_user_comments(self) -> UserComment:
        return self.comments.prefetch_related("user").all()

    @property
    def comment_count_element_id(self) -> str:
        """Returns the id of the element that contains the number of user comments on this object"""
        return f"{self._meta.model_name}-{self.id}-comment-count"
