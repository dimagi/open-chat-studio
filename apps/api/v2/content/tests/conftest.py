"""Fixtures for the content resource endpoints (#4145)."""

from dataclasses import dataclass
from typing import Any

import pytest

from apps.teams.backends import add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team
from apps.utils.factories.experiment import ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


@dataclass(frozen=True)
class Resource:
    path: str
    factory: Any
    create_body: dict
    patch_field: str

    @property
    def model(self):
        return self.factory._meta.model

    @property
    def list_url(self) -> str:
        return f"/api/v2/{self.path}/"

    def detail_url(self, pk: int) -> str:
        return f"/api/v2/{self.path}/{pk}/"


SOURCE_MATERIAL = Resource(
    path="source-material",
    factory=SourceMaterialFactory,
    create_body={"topic": "Returns policy", "description": "What we refund", "material": "30 days."},
    patch_field="topic",
)
CONSENT_FORM = Resource(
    path="consent-forms",
    factory=ConsentFormFactory,
    create_body={"name": "Interview consent", "consent_text": "Do you agree?"},
    patch_field="name",
)
RESOURCES = [pytest.param(SOURCE_MATERIAL, id="source-material"), pytest.param(CONSENT_FORM, id="consent-forms")]


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


@pytest.fixture()
def team_with_roles(db):
    """`create_default_groups()` is explicit so the DB-backed groups match backends.py even
    though pytest runs with --reuse-db."""
    create_default_groups()
    team = TeamFactory.create()
    set_current_team(team)
    return team


def client_for_role(team, group):
    user = UserFactory.create()
    add_user_to_team(team, user, [group])
    return ApiTestClient(user, team)
