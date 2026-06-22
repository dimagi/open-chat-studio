import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_revert_chatbot_version_view(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experiment"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, name="Original", owner=user)
    version = experiment.create_new_version(make_default=True)
    experiment.name = "Modified"
    experiment.save()

    url = reverse("chatbots:revert-version", args=[team.slug, experiment.id, version.version_number])
    response = client.post(url)

    assert response.status_code == 302
    assert response.url.endswith("#versions")
    experiment.refresh_from_db()
    assert experiment.name == "Original"
