"""Tests for the evaluation runs table fragment and its Celery progress polling coordinator."""

import uuid

import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory

# The event the coordinator dispatches on <body> to refresh the table. `#runs-table` in
# evaluation_runs_home.html must subscribe to the same name, otherwise a completed run
# never refreshes the table. htmx cannot reuse `load` here: it handles that trigger at
# init time instead of registering a DOM listener, so dispatching it later is a no-op.
REFRESH_EVENT = "refreshRuns"


@pytest.fixture()
def config(team_with_users):
    return EvaluationConfigFactory.create(team=team_with_users)


def _processing_run(team, config):
    return EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.PROCESSING, job_id=str(uuid.uuid4())
    )


@pytest.mark.django_db()
def test_table_fragment_renders_reversed_progress_url(client, team_with_users, config):
    """The row carries a reversed celery-progress URL rather than a hardcoded path."""
    run = _processing_run(team_with_users, config)

    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])
    content = client.get(url).content.decode()

    expected = reverse("celery_progress:task_status", args=[run.job_id])
    assert f'data-progress-url="{expected}"' in content


@pytest.mark.django_db()
def test_table_fragment_has_single_polling_coordinator(client, team_with_users, config):
    """Rows do not carry their own polling script; one coordinator serves them all."""
    for _ in range(3):
        _processing_run(team_with_users, config)

    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])
    content = client.get(url).content.decode()

    assert content.count("data-progress-url=") == 3
    assert content.count("<script>") == 1


@pytest.mark.django_db()
def test_refresh_event_name_matches_between_coordinator_and_page(client, team_with_users, config):
    """The event the coordinator fires is the one #runs-table listens for."""
    _processing_run(team_with_users, config)
    client.force_login(team_with_users.members.first())

    table_url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])
    fragment = client.get(table_url).content.decode()
    assert f"htmx.trigger(document.body, '{REFRESH_EVENT}')" in fragment

    home_url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])
    page = client.get(home_url).content.decode()
    assert f'hx-trigger="load, {REFRESH_EVENT} from:body"' in page
