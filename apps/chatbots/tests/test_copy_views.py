import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def member(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return user


@pytest.fixture()
def source(team_with_users):
    return ExperimentFactory.create(team=team_with_users, name="Source", seed_message="v1")


def _copy_url(chatbot):
    return reverse("chatbots:copy", args=[chatbot.team.slug, chatbot.id])


@pytest.mark.django_db()
def test_copy_creates_a_new_chatbot_linked_to_its_source(client, member, source):
    response = client.post(_copy_url(source), {"new_name": "Test bot"})

    copy = Experiment.objects.get(name="Test bot")
    assert response.status_code == 302
    assert copy.cloned_from_id == source.id


@pytest.mark.django_db()
def test_overwrite_updates_the_chosen_copy_in_place(client, member, source):
    copy = source.create_new_version(is_copy=True, name="Test bot")
    source.seed_message = "v2"
    source.save()
    chatbot_count = Experiment.objects.get_all().count()

    response = client.post(_copy_url(source), {"overwrite": copy.id})

    assert response.status_code == 302
    assert response.url == reverse("chatbots:single_chatbot_home", args=[source.team.slug, copy.id])
    copy.refresh_from_db()
    assert copy.name == "Test bot"
    assert copy.seed_message == "v2"
    assert Experiment.objects.get_all().count() == chatbot_count


@pytest.mark.django_db()
def test_overwrite_needs_change_permission(client, source):
    viewer = UserFactory()
    MembershipFactory.create(user=viewer, team=source.team)
    viewer.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    client.force_login(viewer)
    copy = source.create_new_version(is_copy=True, name="Test bot")
    source.seed_message = "v2"
    source.save()

    response = client.post(_copy_url(source), {"overwrite": copy.id})

    assert response.status_code == 403
    copy.refresh_from_db()
    assert copy.seed_message == "v1"


@pytest.mark.parametrize(
    "make_target",
    [
        pytest.param(lambda source: ExperimentFactory.create(team=source.team), id="unrelated-chatbot"),
        pytest.param(
            lambda source: ExperimentFactory.create(team=source.team).create_new_version(is_copy=True, name="Other"),
            id="copy-of-another-chatbot",
        ),
    ],
)
@pytest.mark.django_db()
def test_overwrite_only_accepts_copies_of_this_chatbot(client, member, source, make_target):
    target = make_target(source)
    seed_message = target.seed_message
    source.seed_message = "v2"
    source.save()

    client.post(_copy_url(source), {"overwrite": target.id})

    target.refresh_from_db()
    assert target.seed_message == seed_message


@pytest.mark.django_db()
def test_overwrite_is_refused_while_the_copy_is_publishing(client, member, source):
    copy = source.create_new_version(is_copy=True, name="Test bot")
    copy.acquire_version_operation_lock("publishing")
    source.seed_message = "v2"
    source.save()

    client.post(_copy_url(source), {"overwrite": copy.id})

    copy.refresh_from_db()
    assert copy.seed_message == "v1"
    assert copy.create_version_task_id == "publishing"


@pytest.mark.django_db()
def test_copy_dialog_lists_only_this_chatbots_copies(client, member, source):
    copy = source.create_new_version(is_copy=True, name="Test bot")
    unrelated = ExperimentFactory.create(team=source.team).create_new_version(is_copy=True, name="Unrelated copy")

    response = client.get(reverse("chatbots:single_chatbot_home", args=[source.team.slug, source.id]))

    content = response.content.decode()
    assert "Overwrite an earlier copy" in content
    assert f'<option value="{copy.id}">Test bot</option>' in content
    assert f'<option value="{unrelated.id}">' not in content
