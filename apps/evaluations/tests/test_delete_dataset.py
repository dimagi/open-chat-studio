import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset
from apps.utils.factories.evaluations import EvaluationDatasetFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_delete_dataset(client, team_with_users):
    user = team_with_users.members.first()
    dataset = EvaluationDatasetFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:dataset_delete", args=[team_with_users.slug, dataset.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not EvaluationDataset.objects.filter(id=dataset.id).exists()


@pytest.mark.django_db()
def test_delete_dataset_without_delete_perm_is_forbidden(client, team_with_users):
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationDataset),
        codename="view_evaluationdataset",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    dataset = EvaluationDatasetFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:dataset_delete", args=[team_with_users.slug, dataset.id])
    response = client.delete(url)

    assert response.status_code == 403
    assert EvaluationDataset.objects.filter(id=dataset.id).exists()
