import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.experiments.services import publish_version


@pytest.fixture()
def chatbot(experiment):
    """A working chatbot with two versions; v1 is published."""
    experiment.create_new_version()
    experiment.create_new_version()
    return experiment


def published_states(chatbot):
    return [(v.version_number, v.is_default_version) for v in chatbot.versions.order_by("version_number")]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("version_number", "expected"),
    [
        pytest.param(2, [(1, False), (2, True)], id="moves-flag-to-new-version"),
        pytest.param(1, [(1, True), (2, False)], id="already-published-is-unchanged"),
    ],
)
def test_publish_version(chatbot, version_number, expected):
    publish_version(chatbot.versions.get(version_number=version_number))

    assert published_states(chatbot) == expected


@pytest.mark.django_db()
def test_publish_already_published_version_writes_nothing(chatbot):
    v1 = chatbot.versions.get(version_number=1)

    with CaptureQueriesContext(connection) as ctx:
        publish_version(v1)

    assert not [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("UPDATE")]


@pytest.mark.django_db()
def test_set_default_experiment_view_publishes_version(client, chatbot):
    client.force_login(chatbot.team.members.first())
    url = reverse(
        "experiments:set-default-experiment",
        kwargs={"team_slug": chatbot.team.slug, "experiment_id": chatbot.id, "version_number": 2},
    )

    response = client.post(url)

    assert response.status_code == 302
    assert published_states(chatbot) == [(1, False), (2, True)]
