"""A JSON body over DATA_UPLOAD_MAX_MEMORY_SIZE must fail as JSON, not as Django's plain-text 400.

DRF reads ``request.body`` when parsing with the JSON parser, so Django's cap applies to every API
endpoint. ``RequestDataTooBig`` is a ``SuspiciousOperation``, which DRF's own handler declines;
``api_exception_handler`` turns it into a 413 whose ``detail`` names the configured limit.
"""

import json

import pytest
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def small_limit(settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    return 1024


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "url_for",
    [
        pytest.param(lambda experiment: reverse("api:session-list"), id="session-create"),
        pytest.param(lambda experiment: reverse("api:participant-data"), id="participant-data"),
        pytest.param(
            lambda experiment: reverse("api:openai-chat-completions", kwargs={"experiment_id": experiment.public_id}),
            id="openai-completions",
        ),
    ],
)
def test_oversized_body_returns_json_413(small_limit, url_for):
    """The endpoints whose JSON bodies are genuinely unbounded.

    One global handler serves all of them, so this is really one behaviour repeated -- but a
    per-view parser or renderer override would break it for that view alone.
    """
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    client = ApiTestClient(team.members.first(), team)

    response = client.post(
        url_for(experiment),
        data=json.dumps({"padding": "x" * (small_limit + 1024)}),
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {"detail": f"Request body too large. The maximum is {small_limit} bytes."}


@pytest.mark.django_db()
def test_multi_megabyte_state_is_accepted_under_the_configured_limit():
    """The configured limit, not Django's 2.5 MB default, is what session state is measured against."""
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    client = ApiTestClient(team.members.first(), team)

    response = client.post(
        reverse("api:session-list"),
        data=json.dumps({"experiment": str(experiment.public_id), "state": {"blob": "x" * 3 * 1024 * 1024}}),
        content_type="application/json",
    )

    assert response.status_code == 201
