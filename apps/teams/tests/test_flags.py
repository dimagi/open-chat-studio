import pytest
from waffle.testutils import override_flag

from apps.teams.models import Flag
from apps.teams.utils import flag_is_active_for_team
from apps.teams.views.feature_flags import FeatureFlagForm
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("flag_name", "everyone", "team_in_m2m", "expected"),
    [
        pytest.param("flag_precedence_1", True, False, True, id="everyone-true-wins-without-membership"),
        pytest.param("flag_precedence_2", True, True, True, id="everyone-true-wins-with-membership"),
        pytest.param("flag_precedence_3", False, True, True, id="everyone-false-defers-to-membership"),
        pytest.param("flag_precedence_4", False, False, False, id="everyone-false-without-membership-is-off"),
        pytest.param("flag_precedence_5", None, True, True, id="unset-defers-to-membership"),
        pytest.param("flag_precedence_6", None, False, False, id="unset-without-membership-is-off"),
    ],
)
def test_is_active_for_team_everyone_precedence(request, flag_name, everyone, team_in_m2m, expected):
    """`everyone=True` is a global rollout and wins over team membership.

    `everyone=False` is the value every flag-creation path stores for "no global override"
    (see #4321), so it defers to the team M2M rather than switching the flag off.
    """
    team = TeamFactory.create()
    flag = Flag.objects.create(name=flag_name, everyone=everyone)
    request.addfinalizer(flag.flush)
    if team_in_m2m:
        flag.teams.add(team)
    flag.flush()
    assert flag.is_active_for_team(team) is expected


@pytest.mark.django_db()
def test_everyone_true_applies_when_no_team_is_given(request):
    """`everyone` overrides everything in waffle's precedence, including having no team at all."""
    flag = Flag.objects.create(name="flag_precedence_no_team", everyone=True)
    request.addfinalizer(flag.flush)
    assert flag.is_active_for_team(None) is True


@pytest.mark.django_db()
def test_override_flag_reaches_team_scoped_checks():
    """`override_flag` sets `everyone=True`, so team-scoped checks must honour it."""
    team = TeamFactory.create()
    with override_flag("flag_overridden", active=True):
        assert Flag.get("flag_overridden").is_active_for_team(team) is True


@pytest.mark.django_db()
class TestFeatureFlagFormSave:
    """The team settings screen must only write M2M changes for flags the admin actually toggled."""

    def _flag(self, request, **kwargs):
        flag = Flag.objects.create(name="flag_events", **kwargs)
        request.addfinalizer(flag.flush)
        flag.flush()
        return flag

    def test_saving_unchanged_does_not_enrol_team_in_global_rollout(self, request, team_with_users):
        """A flag on for everyone renders ticked; re-saving the screen must not add the team to
        the M2M, where the membership would outlive the end of the rollout."""
        flag = self._flag(request, everyone=True)
        form = FeatureFlagForm({"flag_events": "on"}, team=team_with_users)
        assert form.is_valid()
        form.save()
        assert not flag.teams.filter(pk=team_with_users.pk).exists()

    def test_checking_a_flag_enrols_the_team(self, request, team_with_users):
        flag = self._flag(request)
        form = FeatureFlagForm({"flag_events": "on"}, team=team_with_users)
        assert form.is_valid()
        form.save()
        assert flag.teams.filter(pk=team_with_users.pk).exists()

    def test_unchecking_a_flag_removes_the_team(self, request, team_with_users):
        flag = self._flag(request)
        flag.teams.add(team_with_users)
        flag.flush()
        form = FeatureFlagForm({}, team=team_with_users)
        assert form.is_valid()
        form.save()
        assert not flag.teams.filter(pk=team_with_users.pk).exists()


@pytest.mark.django_db()
def test_flag_is_active_for_team_utility(request, team_flag):
    """`flag_is_active_for_team(team, name)` is the team-scoped counterpart of
    `waffle.flag_is_active(request, name)`: same precedence, addressed by team."""
    team = TeamFactory.create()
    other_team = TeamFactory.create()
    flag = team_flag("flag_utility_check", team)
    request.addfinalizer(flag.flush)
    assert flag_is_active_for_team(team, "flag_utility_check") is True
    assert flag_is_active_for_team(other_team, "flag_utility_check") is False
