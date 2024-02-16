import dataclasses
import operator
from functools import reduce

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Group, Permission
from django.db import models

from apps.teams import roles
from apps.teams.models import Membership
from apps.teams.utils import get_current_team

SUPER_ADMIN_GROUP = "Super Admin"
TEAM_ADMIN_GROUP = "Team Admin"
EXPERIMENT_ADMIN_GROUP = "Experiment Admin"
ANALYSIS_ADMIN_GROUP = "Analysis Admin"
ANALYSIS_USER_GROUP = "Analysis Users"
ASSISTANT_ADMIN_GROUP = "Assistant Admin"
CHAT_VIEWER_GROUP = "Chat Viewer"

NORMAL_USER_GROUPS = [
    EXPERIMENT_ADMIN_GROUP,
    ANALYSIS_ADMIN_GROUP,
    ASSISTANT_ADMIN_GROUP,
    CHAT_VIEWER_GROUP,
]


class PermissionCheckBackend(ModelBackend):
    """Check that permissions exist when in DEBUG mode"""

    def has_perm(self, user_obj, perm, obj=None):
        if settings.DEBUG:
            app_label, codename = perm.split(".")
            if not Permission.objects.filter(content_type__app_label=app_label, codename=codename).exists():
                raise Exception(f"Permission not found {perm}")
        return False  # pass check to next backend


class TeamBackend(ModelBackend):
    def _get_group_permissions(self, user_obj):
        current_team = get_current_team()
        if not current_team:
            return Permission.objects.none()

        return Permission.objects.filter(group__membership__team=current_team, group__membership__user=user_obj)


# Mapping of app labels to content types which are covered by OCS permissions
CONTENT_TYPES = {
    "analysis": ["analysis", "rungroup", "analysisrun", "resource"],
    "assistants": ["openaiassistant"],
    "channels": ["experimentchannel"],
    "chat": ["chat", "chatmessage"],
    "experiments": [
        "consentform",
        "experiment",
        "experimentsession",
        "noactivitymessageconfig",
        "participant",
        "promptbuilderhistory",
        "safetylayer",
        "sourcematerial",
        "survey",
        "syntheticvoice",
    ],
    "files": ["file"],
    "service_providers": ["authprovider", "llmprovider", "voiceprovider", "messagingprovider"],
    "teams": ["invitation", "membership", "team"],
}

CUSTOM_PERMISSIONS = {"experiments": ["invite_participants", "download_chats"]}

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
class CustomPermissionSetDef:
    app_label: str
    permissions: list[str]

    @property
    def codenames(self):
        return self.permissions


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
    GroupDef(
        SUPER_ADMIN_GROUP,
        [AppPermSetDef(app_label, ALL) for app_label in CONTENT_TYPES]
        + [CustomPermissionSetDef(app_label, CUSTOM_PERMISSIONS[app_label]) for app_label in CUSTOM_PERMISSIONS],
    ),
    GroupDef(
        TEAM_ADMIN_GROUP,
        [
            AppPermSetDef("teams", ALL),
            AppPermSetDef("service_providers", ALL),
        ],
    ),
    GroupDef(
        EXPERIMENT_ADMIN_GROUP,
        [
            AppPermSetDef("experiments", ALL),
            AppPermSetDef("channels", ALL),
            CustomPermissionSetDef("experiments", CUSTOM_PERMISSIONS["experiments"]),
        ],
    ),
    GroupDef(
        CHAT_VIEWER_GROUP,
        [
            AppPermSetDef("chat", [VIEW]),
        ],
    ),
    GroupDef(
        ANALYSIS_ADMIN_GROUP,
        [
            AppPermSetDef("analysis", ALL),
        ],
    ),
    GroupDef(
        ANALYSIS_USER_GROUP,
        [
            ModelPermSetDef("analysis", "analysis", [VIEW]),
            ModelPermSetDef("analysis", "rungroup", [VIEW, CHANGE, ADD]),
            ModelPermSetDef("analysis", "analysisrun", [VIEW, CHANGE, ADD]),
            ModelPermSetDef("analysis", "resource", [VIEW, CHANGE, ADD]),
        ],
    ),
    GroupDef(
        ASSISTANT_ADMIN_GROUP,
        [
            AppPermSetDef("assistants", ALL),
            AppPermSetDef("files", ALL),
        ],
    ),
]


def create_default_groups():
    """
    Creates the default groups for the team.
    """
    for group_def in GROUPS:
        group_def.update_or_create()


def make_user_team_owner(team, user) -> Membership:
    membership = Membership.objects.create(team=team, user=user, role=roles.ROLE_ADMIN)
    membership.groups.set(get_team_owner_groups())
    return membership


def add_user_to_team(team, user, groups=None) -> Membership:
    membership = Membership.objects.create(team=team, user=user, role=roles.ROLE_MEMBER)
    if groups:
        membership.groups.set(groups)
    return membership


def get_team_owner_groups():
    return [Group.objects.get(name=SUPER_ADMIN_GROUP)]


def get_groups():
    return {group.name: group for group in Group.objects.all()}
