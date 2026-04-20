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
    "account",
    "admin",
    "allauth",
    "mfa",  # allauth.mfa app label
    "api",
    "analysis",  # TODO: delete once the app is completely removed
    "audit",
    "auth",
    "cache",  # heath_check.cache
    "celery",  # heath_check.celery
    "celery_progress",
    "contenttypes",
    "corsheaders",
    "data_migrations",
    "db",  # heath_check.db
    "django_celery_beat",
    "django_cleanup",
    "django_htmx",
    "django_browser_reload",
    "django_tables2",
    "django_watchfiles",
    "documents",  # ignore for now - may be added later
    "dashboard",
    "drf_spectacular",
    "field_audit",
    "audit_tests",
    "forms",
    "generics",
    "health_check",
    "help",
    "humanize",
    "messages",
    "microsoft",  # allauth
    "redis",  # heath_check.redis
    "rest_framework",
    "rest_framework_api_key",
    "runserver_nostatic",
    "sessions",
    "site_admin",
    "sitemaps",
    "sites",
    "slack",
    "socialaccount",
    "sso",
    "staticfiles",
    "taggit",
    "template_partials",
    "tz_detect",
    "users",
    "utils_tests",
    "waffle",
    "web",
    "silk",
    "oauth2_provider",
}

IGNORE_MODELS = {"teams": {"flag"}}


def test_missing_content_types():
    app_labels = set(apps.app_configs) - set(IGNORE_APPS)
    missing = app_labels - set(CONTENT_TYPES)
    assert not missing, f"Missing content types for {missing}"

    for app_label in apps.app_configs:
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
            AppPermSetDef("bot_channels", [VIEW]),
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
        ("chat attachment", "add_chatattachment"),
        ("chat attachment", "change_chatattachment"),
        ("chat attachment", "delete_chatattachment"),
        ("chat attachment", "view_chatattachment"),
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
        ("chat attachment", "add_chatattachment"),
        ("chat attachment", "change_chatattachment"),
        ("chat attachment", "delete_chatattachment"),
        ("chat attachment", "view_chatattachment"),
        ("chat message", "add_chatmessage"),
        ("chat message", "change_chatmessage"),
        ("chat message", "delete_chatmessage"),
        ("chat message", "view_chatmessage"),
    ]


def test_chatbot_admin_documents_permissions_regression():
    """Regression test for #3158: CHATBOT_ADMIN_GROUP must use AppPermSetDef for documents.

    CustomPermissionSetDef returns raw CRUD verbs ("view", "change", etc.) which don't match
    Django's auto-generated permission codenames ("view_collection", etc.), resulting in zero
    permissions being granted for documents.
    """
    from apps.teams.backends import CHATBOT_ADMIN_GROUP

    chatbot_admin = next(g for g in GROUPS if g.name == CHATBOT_ADMIN_GROUP)
    documents_def = next(pd for pd in chatbot_admin.permission_defs if pd.app_label == "documents")

    codenames = documents_def.codenames
    raw_crud_verbs = {"view", "change", "delete", "add"}

    # Codenames must not be raw CRUD verbs (the bug: CustomPermissionSetDef returns these directly)
    assert not raw_crud_verbs.intersection(codenames), (
        "documents permissions must not be raw CRUD verbs; use AppPermSetDef not CustomPermissionSetDef"
    )

    # Codenames must follow the "action_model" pattern for document models
    assert "view_collection" in codenames
    assert "add_collection" in codenames
    assert "change_collection" in codenames
    assert "delete_collection" in codenames


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
                assert permission[0] in CUSTOM_PERMISSIONS.get(app.label, {}), (
                    "permissions not in CUSTOM_PERMISSIONS dict"
                )
                assert permission[0] in mapped_permissions, "permissions must be mapped to at least one group"
