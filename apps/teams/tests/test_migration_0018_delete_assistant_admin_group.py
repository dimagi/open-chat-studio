import importlib

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.teams.models import Flag, Invitation, Membership
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory

_migration = importlib.import_module("apps.teams.migrations.0018_delete_assistant_admin_group")
delete_assistant_admin_group = _migration.delete_assistant_admin_group


class FakeSchemaEditor:
    """Stands in for the schema editor a RunPython operation receives."""

    connection = connection

    def execute(self, sql, params=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run():
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("teams", "0017_team_require_mfa")])
    delete_assistant_admin_group(state.apps, FakeSchemaEditor())


@pytest.fixture()
def assistant_admin():
    """Created explicitly: the suite runs --reuse-db, so post_migrate-seeded groups are not dependable."""
    group, _ = Group.objects.get_or_create(name="Assistant Admin")
    return group


@pytest.mark.django_db()
def test_deletes_the_group(assistant_admin):
    _run()

    assert not Group.objects.filter(name="Assistant Admin").exists()


@pytest.mark.django_db()
def test_detaches_the_group_from_memberships_and_leaves_the_membership(assistant_admin):
    other, _ = Group.objects.get_or_create(name="Chatbot Admin")
    membership = MembershipFactory.create(groups=[assistant_admin, other])

    _run()

    membership.refresh_from_db()
    assert Membership.objects.filter(pk=membership.pk).exists()
    assert [group.name for group in membership.groups.all()] == ["Chatbot Admin"]


@pytest.mark.django_db()
def test_detaches_the_group_from_invitations(assistant_admin):
    invitation = Invitation.objects.create(
        team=TeamFactory.create(), email="new@example.org", invited_by=UserFactory.create()
    )
    invitation.groups.add(assistant_admin)

    _run()

    invitation.refresh_from_db()
    assert not invitation.groups.exists()


@pytest.mark.django_db()
def test_detaches_the_group_from_flags(assistant_admin):
    flag = Flag.objects.create(name="flag_assistant_admin_probe")
    flag.groups.add(assistant_admin)

    _run()

    flag.refresh_from_db()
    assert Flag.objects.filter(pk=flag.pk).exists()
    assert not flag.groups.exists()


@pytest.mark.django_db()
def test_is_a_noop_when_the_group_is_already_gone():
    Group.objects.filter(name="Assistant Admin").delete()

    _run()

    assert not Group.objects.filter(name="Assistant Admin").exists()


@pytest.mark.django_db()
def test_detaches_the_group_from_users(assistant_admin):
    """Staff can assign the group directly through the Django admin's user form."""
    user = UserFactory.create()
    user.groups.add(assistant_admin)

    _run()

    through = get_user_model().groups.through
    assert not through.objects.filter(group_id=assistant_admin.id).exists()
