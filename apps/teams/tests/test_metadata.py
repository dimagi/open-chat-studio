import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.teams.metadata import get_team_metadata_fields


def test_returns_normalized_fields(settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner", "extra": "ignored"}]
    assert get_team_metadata_fields() == [{"key": "team_owner", "label": "Team Owner"}]


@pytest.mark.parametrize(
    "bad_setting",
    [
        pytest.param("not-a-list", id="not-a-list"),
        pytest.param([{"key": "team_owner"}], id="missing-label"),
        pytest.param([{"key": "", "label": "Team Owner"}], id="empty-key"),
        pytest.param(["not-a-dict"], id="not-a-dict"),
    ],
)
def test_rejects_malformed_setting(settings, bad_setting):
    settings.TEAM_METADATA_FIELDS = bad_setting
    with pytest.raises(ImproperlyConfigured):
        get_team_metadata_fields()
