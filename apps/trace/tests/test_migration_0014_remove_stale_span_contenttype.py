import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from apps.annotations.models import CustomTaggedItem, UserComment
from apps.utils.factories.annotations import TagFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

_migration = importlib.import_module("apps.trace.migrations.0014_remove_stale_span_contenttype")
remove_stale_span_contenttype = _migration.remove_stale_span_contenttype


class FakeSchemaEditor:
    connection = connection


def _run_migration():
    remove_stale_span_contenttype(django_apps, FakeSchemaEditor())


@pytest.mark.django_db()
def test_removes_stale_span_contenttype_and_dangling_dependents():
    team = TeamFactory()
    user = UserFactory()
    span_ct = ContentType.objects.create(app_label="trace", model="span")

    # Rows that dangle off the stale content type via generic/direct FKs. `object_id` points at a
    # span row that no longer exists — that's exactly the dangling state 0008 left behind.
    tagged_item = CustomTaggedItem.objects.create(
        team=team, user=user, tag=TagFactory(team=team), content_type=span_ct, object_id=999
    )
    comment = UserComment.objects.create(team=team, user=user, comment="stale", content_type=span_ct, object_id=999)
    permission = Permission.objects.create(content_type=span_ct, codename="view_span", name="Can view span")

    _run_migration()

    assert not ContentType.objects.filter(app_label="trace", model="span").exists()
    assert not CustomTaggedItem.objects.filter(pk=tagged_item.pk).exists()
    assert not UserComment.objects.filter(pk=comment.pk).exists()
    assert not Permission.objects.filter(pk=permission.pk).exists()


@pytest.mark.django_db()
def test_leaves_other_contenttypes_and_their_dependents_untouched():
    team = TeamFactory()
    user = UserFactory()
    other_ct = ContentType.objects.get_for_model(UserComment)
    comment = UserComment.objects.create(team=team, user=user, comment="keep me", content_type=other_ct, object_id=1)

    _run_migration()

    assert ContentType.objects.filter(pk=other_ct.pk).exists()
    assert UserComment.objects.filter(pk=comment.pk).exists()


@pytest.mark.django_db()
def test_is_a_noop_when_span_contenttype_already_gone():
    assert not ContentType.objects.filter(app_label="trace", model="span").exists()

    # Running against a DB that never had the stale row must not raise.
    _run_migration()

    assert not ContentType.objects.filter(app_label="trace", model="span").exists()
