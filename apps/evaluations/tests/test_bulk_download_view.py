"""Tests for the bulk-export button and its Celery progress bar."""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.utils.factories.evaluations import EvaluationConfigFactory


@pytest.fixture()
def config(team_with_users):
    return EvaluationConfigFactory.create(team=team_with_users)


def _start_export(client, team, config, task_id):
    client.force_login(team.members.first())
    url = reverse("evaluations:evaluation_bulk_download_start", args=[team.slug, config.id])
    with patch("apps.evaluations.views.evaluation_config_views.export_evaluation_bulk_results_task.delay") as delay:
        delay.return_value.id = task_id
        return client.post(url).content.decode()


@pytest.mark.django_db()
def test_start_bulk_download_renders_a_progress_bar(client, team_with_users, config):
    """Starting the export swaps in the bar the task's progress feeds."""
    content = _start_export(client, team_with_users, config, task_id=str(uuid.uuid4()))

    assert 'id="eval-export-progress-bar"' in content
    assert 'id="eval-export-progress-message"' in content


@pytest.mark.django_db()
def test_progress_bar_polls_the_reversed_celery_progress_url(client, team_with_users, config):
    """The poll URL is reversed rather than hardcoded, so it survives a urlconf change."""
    task_id = str(uuid.uuid4())

    content = _start_export(client, team_with_users, config, task_id=task_id)

    assert reverse("celery_progress:task_status", args=[task_id]) in content


@pytest.mark.django_db()
def test_runs_home_loads_the_progress_library(client, team_with_users, config):
    """The partial's inline script calls CeleryProgressBar, so the page must load it."""
    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])

    content = client.get(url).content.decode()

    assert "celery_progress/celery_progress.js" in content


@pytest.mark.django_db()
def test_start_button_id_matches_the_one_the_script_clears(client, team_with_users, config):
    """Every terminal state hides the button by id. Renaming one side only would leave a
    disabled "Generating" button sitting next to the download link."""
    content = _start_export(client, team_with_users, config, task_id=str(uuid.uuid4()))

    assert 'id="eval-bulk-download-button"' in content
    assert 'getElementById("eval-bulk-download-button")' in content
