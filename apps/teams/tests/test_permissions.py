import pytest
from django.apps import apps
from django.contrib.auth.models import Group

from apps.teams.backends import (
    ALL,
    CHANGE,
    CONTENT_TYPES,
    CUSTOM_PERMISSIONS,
    GROUPS,
    VIEW,
    AppPermSetDef,
    GroupDef,
    ModelPermSetDef,
)


def test_content_types():
    for app_label, models in CONTENT_TYPES.items():
        assert app_label in apps.all_models
        for model in models:
            assert model in apps.all_models[app_label]


IGNORE_APPS = {
    "api",
    "users",
    "sites",
    "otp_static",
    "socialaccount",
    "allauth",
    "django_otp",
    "hijack_admin",
    "sessions",
    "forms",
    "drf_spectacular",
    "allauth_2fa",
    "celery_progress",
    "auth",
    "staticfiles",
    "rest_framework",
    "llm_providers",
    "admin",
    "hijack",
    "otp_totp",
    "contenttypes",
    "web",
    "sitemaps",
    "rest_framework_api_key",
    "waffle",
    "django_tables2",
    "django_celery_beat",
    "messages",
    "runserver_nostatic",
    "account",
    "generics",
    "humanize",
    "field_audit",
}

IGNORE_MODELS = {"teams": {"flag"}}


def test_missing_content_types():
    app_labels = set(apps.all_models) - set(IGNORE_APPS)
    missing = app_labels - set(CONTENT_TYPES)
    assert not missing, f"Missing content types for {missing}"

    for app_label in apps.all_models:
        if app_label in IGNORE_APPS:
            continue
        models = {m.__name__.lower() for m in apps.get_app_config(app_label).get_models()}
        models -= IGNORE_MODELS.get(app_label, set())
        missing = models - set(CONTENT_TYPES[app_label])
        assert not missing, f"Missing content types for {missing} in {app_label}"


@pytest.mark.django_db()
def test_group_def():
    group_def = GroupDef(
        "dummy",
        [
            AppPermSetDef("chat", ALL),
            AppPermSetDef("channels", [VIEW]),
            ModelPermSetDef("teams", "team", [VIEW, CHANGE]),
        ],
    )
    group_def.update_or_create()
    group = Group.objects.get(name=group_def.name)
    assert [(p.content_type.name, p.codename) for p in group.permissions.all()] == [
        ("experiment channel", "view_experimentchannel"),
        ("chat", "add_chat"),
        ("chat", "change_chat"),
        ("chat", "delete_chat"),
        ("chat", "view_chat"),
        ("chat message", "add_chatmessage"),
        ("chat message", "change_chatmessage"),
        ("chat message", "delete_chatmessage"),
        ("chat message", "view_chatmessage"),
        ("team", "change_team"),
        ("team", "view_team"),
    ]

    group_def.permission_defs = [
        AppPermSetDef("chat", ALL),
    ]
    group_def.update_or_create()
    group.refresh_from_db()
    assert [(p.content_type.name, p.codename) for p in group.permissions.all()] == [
        ("chat", "add_chat"),
        ("chat", "change_chat"),
        ("chat", "delete_chat"),
        ("chat", "view_chat"),
        ("chat message", "add_chatmessage"),
        ("chat message", "change_chatmessage"),
        ("chat message", "delete_chatmessage"),
        ("chat message", "view_chatmessage"),
    ]


def test_custom_permissions():
    mapped_permissions = [
        permission
        for group_def in GROUPS
        for permission_def in group_def.permission_defs
        for permission in permission_def.codenames
    ]
    for app in apps.get_app_configs():
        for model in app.get_models():
            for permission in model._meta.permissions:
                assert permission[0] in CUSTOM_PERMISSIONS.get(
                    app.label, {}
                ), "permissions not in CUSTOM_PERMISSIONS dict"
                assert permission[0] in mapped_permissions, "permissions must be mapped to at least one group"
