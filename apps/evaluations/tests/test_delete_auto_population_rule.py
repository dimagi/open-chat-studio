import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
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


@pytest.mark.django_db()
def test_delete_auto_population_rule_for_other_team_returns_404(client, team_with_users):
    user = team_with_users.members.first()
    other_team = TeamFactory.create()
    rule = DatasetAutoPopulationRuleFactory.create(team=other_team)

    client.force_login(user)
    url = reverse("evaluations:auto_population_rule_delete", args=[team_with_users.slug, rule.id])
    response = client.delete(url)

    assert response.status_code == 404
    assert DatasetAutoPopulationRule.objects.filter(id=rule.id).exists()


@pytest.mark.django_db()
def test_delete_auto_population_rule_requires_change_dataset_perm_not_delete_perm(client, team_with_users):
    """Delete is gated on change_evaluationdataset, not a delete-specific permission.

    Non-obvious choice worth locking down: a user granted only
    delete_datasetautopopulationrule (no change_evaluationdataset) must still be forbidden.
    """
    delete_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(DatasetAutoPopulationRule),
        codename="delete_datasetautopopulationrule",
    )
    limited_group = GroupFactory.create(name="evaluations-delete-rule-only")
    limited_group.permissions.add(delete_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    rule = DatasetAutoPopulationRuleFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:auto_population_rule_delete", args=[team_with_users.slug, rule.id])
    response = client.delete(url)

    assert response.status_code == 403
    assert DatasetAutoPopulationRule.objects.filter(id=rule.id).exists()
