from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from taggit.models import TagBase, TaggedItem

from apps.teams.models import BaseTeamModel


class Tag(TagBase, BaseTeamModel):
    name = models.CharField(verbose_name=pgettext_lazy("A tag name", "name"), max_length=100)

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        unique_together = ("team", "name")
        ordering = ["name"]


class CustomTaggedItem(TaggedItem, BaseTeamModel):
    user = models.ForeignKey("users.CustomUser", on_delete=models.DO_NOTHING)
