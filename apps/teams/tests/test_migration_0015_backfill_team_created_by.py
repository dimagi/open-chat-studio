import importlib
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from field_audit.models import AuditAction

from apps.teams.models import Membership
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
def test_backfill_uses_earliest_membership_and_leaves_teams_without_members_null():
    migration = importlib.import_module("apps.teams.migrations.0015_team_created_by")
    team = TeamFactory(created_by=None)
    team_without_members = TeamFactory(created_by=None)
    known_creator = UserFactory()
    team_with_known_creator = TeamFactory(created_by=known_creator)
    MembershipFactory(team=team_with_known_creator)
    later_membership = MembershipFactory(team=team)
    earlier_membership = MembershipFactory(team=team)
    now = timezone.now()
    Membership.objects.filter(pk=later_membership.pk).update(created_at=now, audit_action=AuditAction.IGNORE)
    Membership.objects.filter(pk=earlier_membership.pk).update(
        created_at=now - timedelta(days=1), audit_action=AuditAction.IGNORE
    )

    historical_apps = MigrationExecutor(connection).loader.project_state().apps
    migration.backfill_team_created_by(historical_apps, None)

    team.refresh_from_db()
    team_without_members.refresh_from_db()
    team_with_known_creator.refresh_from_db()
    assert team.created_by == earlier_membership.user
    assert team_without_members.created_by is None
    assert team_with_known_creator.created_by == known_creator
