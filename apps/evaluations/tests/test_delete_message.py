import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationMessage, EvaluationRunStatus, EvaluationRunType
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(EvaluationRunStatus.PENDING, id="pending"),
        pytest.param(EvaluationRunStatus.PROCESSING, id="processing"),
    ],
)
def test_delete_message_blocked_while_dataset_run_in_flight(status, client, team_with_users):
    user = team_with_users.members.first()
    message = EvaluationMessageFactory.create()
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[message])
    config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
    EvaluationRunFactory.create(team=team_with_users, config=config, status=status)

    client.force_login(user)
    url = reverse("evaluations:delete_message", args=[team_with_users.slug, message.id])
    response = client.delete(url)

    assert response.status_code == 409
    assert EvaluationMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
def test_delete_message_blocked_while_scoped_delta_run_in_flight(client, team_with_users):
    """Isolates the scoped_messages OR-clause: the run's config uses a DIFFERENT dataset,
    so only the scoped_messages relationship (not config__dataset__messages) can block."""
    user = team_with_users.members.first()
    message = EvaluationMessageFactory.create()
    # The dataset the message belongs to (needed for the team-scoped view lookup).
    EvaluationDatasetFactory.create(team=team_with_users, messages=[message])
    # In-flight DELTA run whose config points at a different dataset that does NOT contain the message.
    other_dataset = EvaluationDatasetFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, dataset=other_dataset)
    run = EvaluationRunFactory.create(
        team=team_with_users,
        config=config,
        status=EvaluationRunStatus.PROCESSING,
        type=EvaluationRunType.DELTA,
    )
    run.scoped_messages.add(message)

    client.force_login(user)
    url = reverse("evaluations:delete_message", args=[team_with_users.slug, message.id])
    response = client.delete(url)

    assert response.status_code == 409
    assert EvaluationMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(EvaluationRunStatus.COMPLETED, id="completed"),
        pytest.param(EvaluationRunStatus.FAILED, id="failed"),
    ],
)
def test_delete_message_allowed_when_runs_terminal(status, client, team_with_users):
    user = team_with_users.members.first()
    message = EvaluationMessageFactory.create()
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[message])
    config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
    EvaluationRunFactory.create(team=team_with_users, config=config, status=status)

    client.force_login(user)
    url = reverse("evaluations:delete_message", args=[team_with_users.slug, message.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not EvaluationMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
def test_delete_message_for_other_team_returns_404(client, team_with_users):
    user = team_with_users.members.first()
    other_team = TeamFactory.create()
    message = EvaluationMessageFactory.create()
    EvaluationDatasetFactory.create(team=other_team, messages=[message])

    client.force_login(user)
    url = reverse("evaluations:delete_message", args=[team_with_users.slug, message.id])
    response = client.delete(url)

    assert response.status_code == 404
    assert EvaluationMessage.objects.filter(id=message.id).exists()
