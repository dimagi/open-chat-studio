import dataclasses
import operator
from functools import reduce

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Group, Permission
from django.db import models

from apps.teams.utils import get_current_team


class TeamBackend(ModelBackend):
    def _get_group_permissions(self, user_obj):
        current_team = get_current_team()
        if not current_team:
            return Permission.objects.none()

        return Permission.objects.filter(group__membership__team=current_team, group__membership__user=user_obj)


CONTENT_TYPES = {
    "channels": ["experimentchannel"],
    "chat": ["chat", "chatmessage"],
    "experiments": [
        "consentform",
        "experiment",
        "experimentsession",
        "noactivitymessageconfig",
        "participant",
        "prompt",
        "promptbuilderhistory",
        "safetylayer",
        "sourcematerial",
        "survey",
        "syntheticvoice",
    ],
    "service_providers": ["llmprovider", "voiceprovider"],
    "teams": ["invitation", "membership", "team"],
}

VIEW = "view"
CHANGE = "change"
DELETE = "delete"
ADD = "add"
ALL = [VIEW, CHANGE, DELETE, ADD]


@dataclasses.dataclass
class AppPermSetDef:
    app_label: str
    permissions: list[str]

    @property
    def codenames(self):
        return [f"{permission}_{model}" for model in CONTENT_TYPES[self.app_label] for permission in self.permissions]


@dataclasses.dataclass
class ModelPermSetDef:
    app_label: str
    model: str
    permissions: list[str]

    @property
    def codenames(self):
        return [f"{permission}_{self.model}" for permission in self.permissions]


@dataclasses.dataclass
class GroupDef:
    name: str
    permission_defs: list[ModelPermSetDef | AppPermSetDef]

    def update_or_create(self):
        group, _ = Group.objects.get_or_create(name=self.name)
        group.permissions.set(self.get_permissions())
        return group

    def get_permissions(self):
        filters = reduce(
            operator.or_,
            [
                models.Q(content_type__app_label=perm_set.app_label, codename__in=perm_set.codenames)
                for perm_set in self.permission_defs
            ],
        )
        return Permission.objects.filter(filters)


GROUPS = [
    GroupDef("Super Admin", [AppPermSetDef(app_label, ALL) for app_label in CONTENT_TYPES]),
    GroupDef(
        "Team Admin",
        [
            AppPermSetDef("teams", ALL),
            AppPermSetDef("service_providers", ALL),
        ],
    ),
    GroupDef(
        "Experiment Admin",
        [
            AppPermSetDef("experiments", ALL),
            AppPermSetDef("channels", ALL),
        ],
    ),
    GroupDef(
        "Chat Viewer",
        [
            AppPermSetDef("chat", [VIEW]),
        ],
    ),
]

_groups_created = False


def create_default_groups():
    """
    Creates the default groups for the team.
    """
    global _groups_created
    if _groups_created:
        return

    for group_def in GROUPS:
        group_def.update_or_create()

    _groups_created = True
