import pytest
from waffle.testutils import override_flag

from apps.teams.models import Flag
from apps.teams.utils import flag_is_active_for_team
from apps.teams.views.feature_flags import FeatureFlagForm
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("flag_name", "everyone", "team_in_m2m", "expected"),
    [
        pytest.param("flag_precedence_1", True, False, True, id="everyone-true-wins-without-membership"),
        pytest.param("flag_precedence_2", True, True, True, id="everyone-true-wins-with-membership"),
        pytest.param("flag_precedence_3", False, True, False, id="everyone-false-beats-membership"),
        pytest.param("flag_precedence_4", False, False, False, id="everyone-false-without-membership-is-off"),
        pytest.param("flag_precedence_5", None, True, True, id="unset-defers-to-membership"),
        pytest.param("flag_precedence_6", None, False, False, id="unset-without-membership-is-off"),
    ],
)
def test_is_active_for_team_everyone_precedence(request, flag_name, everyone, team_in_m2m, expected):
    """`everyone` is a tri-state global override: `True` is on for everyone, `False` is
    off for everyone, and only `None` defers to the team M2M (see #4321)."""
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
@pytest.mark.parametrize(
    ("flag_name", "everyone", "expected"),
    [
        pytest.param("flag_request_hard_off", False, False, id="everyone-false-beats-membership-on-request-path"),
        pytest.param("flag_request_teams", None, True, id="unset-defers-to-membership-on-request-path"),
    ],
)
def test_is_active_everyone_precedence_on_request_path(request, rf, flag_name, everyone, expected):
    """`is_active` applies the same tri-state as `is_active_for_team`: an explicit
    `everyone=False` switches the flag off even for a team in the M2M."""
    team = TeamFactory.create()
    flag = Flag.objects.create(name=flag_name, everyone=everyone)
    request.addfinalizer(flag.flush)
    flag.teams.add(team)
    flag.flush()
    http_request = rf.get("/")
    http_request.team = team
    assert flag.is_active(http_request) is expected


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

    def test_checking_a_missing_flag_creates_it_without_global_override(self, request, team_with_users):
        """A row minted by the team screen must not carry a global `everyone` decision:
        `False` is now a hard off, so the created row stores `None` and defers to teams."""
        form = FeatureFlagForm({"flag_events": "on"}, team=team_with_users)
        assert form.is_valid()
        form.save()
        flag = Flag.objects.get(name="flag_events")
        request.addfinalizer(flag.flush)
        assert flag.everyone is None
        assert flag.superusers is False
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
def test_create_missing_flags_row_carries_no_global_override(request, settings):
    """`CREATE_MISSING_FLAGS` mints the row with `everyone=None` (no global decision);
    the check itself still answers with `FLAG_DEFAULT` for the missing flag."""
    settings.WAFFLE_CREATE_MISSING_FLAGS = True
    team = TeamFactory.create()
    unsaved = Flag.get("flag_minted_on_demand")
    request.addfinalizer(unsaved.flush)
    assert unsaved.is_active_for_team(team) is False
    row = Flag.objects.get(name="flag_minted_on_demand")
    assert row.everyone is None
    assert row.superusers is False


@pytest.mark.django_db()
def test_create_missing_flags_on_request_path_carries_no_global_override(request, rf, settings):
    """waffle's own mint stores `everyone=FLAG_DEFAULT` (`False`), which is now a hard off
    that would kill later team grants; the model's `superusers=True` default would grant
    superusers blanket access. The minted row must carry neither."""
    settings.WAFFLE_CREATE_MISSING_FLAGS = True
    team = TeamFactory.create()
    unsaved = Flag.get("flag_minted_on_request")
    request.addfinalizer(unsaved.flush)
    http_request = rf.get("/")
    http_request.team = team
    http_request.user = UserFactory.create(is_superuser=True)
    assert unsaved.is_active(http_request) is False
    row = Flag.objects.get(name="flag_minted_on_request")
    assert row.everyone is None
    assert row.superusers is False


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
