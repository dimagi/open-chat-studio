"""Read-only view of what each default group grants, derived from the group definitions."""

from collections import defaultdict
from dataclasses import dataclass

from django.apps import apps
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from apps.teams.backends import (
    ADD,
    CHANGE,
    CONTENT_TYPES,
    DELETE,
    GROUPS,
    VIEW,
    AppPermSetDef,
    CustomPermissionSetDef,
    GroupDef,
    ModelPermSetDef,
)

COLUMNS = [(VIEW, _("View")), (ADD, _("Add")), (CHANGE, _("Edit")), (DELETE, _("Delete"))]


@dataclass(frozen=True)
class PermissionRow:
    app_label: str
    area: str
    #: The models the row covers. `resources` is empty when they are every model in the app.
    models: tuple[str, ...]
    resources: tuple[str, ...]
    actions: frozenset[str]

    @property
    def cells(self) -> list[bool]:
        return [action in self.actions for action, _label in COLUMNS]


@dataclass(frozen=True)
class GroupPermissionTable:
    group: str
    rows: list[PermissionRow]
    other_permissions: list[str]


def get_group_permission_tables(group_defs: list[GroupDef] = GROUPS) -> list[GroupPermissionTable]:
    return [_group_table(group_def) for group_def in group_defs]


def group_permissions_context() -> dict:
    """Context for `teams/components/group_permissions.html`."""
    return {"group_permission_tables": get_group_permission_tables(), "group_permission_columns": COLUMNS}


def _group_table(group_def: GroupDef) -> GroupPermissionTable:
    actions_by_model = defaultdict(set)
    other_permissions = []
    for perm_def in group_def.permission_defs:
        if isinstance(perm_def, CustomPermissionSetDef):
            other_permissions += _custom_permission_names(perm_def)
        elif isinstance(perm_def, AppPermSetDef):
            for model in CONTENT_TYPES[perm_def.app_label]:
                actions_by_model[perm_def.app_label, model].update(perm_def.permissions)
        elif isinstance(perm_def, ModelPermSetDef):
            actions_by_model[perm_def.app_label, perm_def.model].update(perm_def.permissions)

    rows = []
    for app_label in dict.fromkeys(app_label for app_label, _model in actions_by_model):
        rows += _app_rows(app_label, actions_by_model)
    rows.sort(key=lambda row: row.area)
    return GroupPermissionTable(group=group_def.name, rows=rows, other_permissions=other_permissions)


def _app_rows(app_label: str, actions_by_model: dict) -> list[PermissionRow]:
    """One row per distinct set of actions, so models granted the same actions share a row."""
    models_by_actions = defaultdict(list)
    for model in CONTENT_TYPES[app_label]:
        if actions := actions_by_model.get((app_label, model)):
            models_by_actions[frozenset(actions)].append(model)

    area = apps.get_app_config(app_label).verbose_name
    if len(models_by_actions) == 1:
        actions, models = next(iter(models_by_actions.items()))
        if models == CONTENT_TYPES[app_label]:
            return [PermissionRow(app_label, area, tuple(models), resources=(), actions=actions)]

    return [
        PermissionRow(
            app_label,
            area,
            tuple(models),
            resources=tuple(capfirst(apps.get_model(app_label, model)._meta.verbose_name_plural) for model in models),
            actions=actions,
        )
        for actions, models in models_by_actions.items()
    ]


def _custom_permission_names(perm_def: CustomPermissionSetDef) -> list[str]:
    names = {
        codename: name
        for model in apps.get_app_config(perm_def.app_label).get_models()
        for codename, name in model._meta.permissions
    }
    return [names[codename] for codename in perm_def.codenames]
