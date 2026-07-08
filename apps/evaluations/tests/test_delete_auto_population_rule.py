import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_delete_auto_population_rule(client, team_with_users):
    user = team_with_users.members.first()
    rule = DatasetAutoPopulationRuleFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:auto_population_rule_delete", args=[team_with_users.slug, rule.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not DatasetAutoPopulationRule.objects.filter(id=rule.id).exists()


@pytest.mark.django_db()
def test_delete_auto_population_rule_without_change_perm_is_forbidden(client, team_with_users):
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(DatasetAutoPopulationRule),
        codename="view_datasetautopopulationrule",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    rule = DatasetAutoPopulationRuleFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:auto_population_rule_delete", args=[team_with_users.slug, rule.id])
    response = client.delete(url)

    assert response.status_code == 403
    assert DatasetAutoPopulationRule.objects.filter(id=rule.id).exists()
