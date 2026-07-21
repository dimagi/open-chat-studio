import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationMessage, EvaluationRunStatus, EvaluationRunType
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
)


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
    user = team_with_users.members.first()
    message = EvaluationMessageFactory.create()
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[message])
    config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
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
def test_delete_message_allowed_when_runs_terminal(client, team_with_users):
    user = team_with_users.members.first()
    message = EvaluationMessageFactory.create()
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[message])
    config = EvaluationConfigFactory.create(team=team_with_users, dataset=dataset)
    EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)

    client.force_login(user)
    url = reverse("evaluations:delete_message", args=[team_with_users.slug, message.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not EvaluationMessage.objects.filter(id=message.id).exists()
