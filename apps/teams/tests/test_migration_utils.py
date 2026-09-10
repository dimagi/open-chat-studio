import pytest
from django.apps import apps as global_apps
from django.db.migrations.state import ProjectState

from apps.teams.migration_utils import delete_waffle_flag
from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory

FLAG_NAME = "flag_being_deleted"
OTHER_FLAG_NAME = "flag_left_alone"


@pytest.fixture()
def historical_flag_model():
    """The method-less model a migration is handed by `apps.get_model`."""
    return ProjectState.from_apps(global_apps).apps.get_model("teams", "Flag")


def _delete(flag_model):
    delete_waffle_flag(flag_model=flag_model, flag_name=FLAG_NAME)


@pytest.mark.django_db()
def test_deletes_the_flag_and_its_team_grants(historical_flag_model):
    flag = Flag.objects.create(name=FLAG_NAME, everyone=None)
    flag.teams.add(TeamFactory.create())
    Flag.objects.create(name=OTHER_FLAG_NAME, everyone=None)

    _delete(historical_flag_model)

    assert not Flag.objects.filter(name=FLAG_NAME).exists()
    assert not Flag.teams.through.objects.filter(flag_id=flag.pk).exists()
    assert Flag.objects.filter(name=OTHER_FLAG_NAME).exists()


@pytest.mark.django_db()
def test_leaves_no_flag_in_the_cache(historical_flag_model):
    """A cached instance would keep the flag answering checks after its row is gone."""
    flag = Flag.objects.create(name=FLAG_NAME, everyone=True)
    flag.flush()
    assert Flag.get(FLAG_NAME).pk is not None

    _delete(historical_flag_model)

    assert Flag.get(FLAG_NAME).pk is None


@pytest.mark.django_db()
def test_is_a_no_op_when_the_flag_is_already_gone(historical_flag_model):
    """Self-hosters and dev DBs may have deleted the row by hand already."""
    _delete(historical_flag_model)

    assert not Flag.objects.filter(name=FLAG_NAME).exists()
