import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.teams.metadata import get_team_metadata_fields


def test_returns_normalized_fields(settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner", "extra": "ignored"}]
    assert get_team_metadata_fields() == [{"key": "team_owner", "label": "Team Owner", "type": "text"}]


def test_email_field(settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "contact", "label": "Contact", "type": "email"}]
    assert get_team_metadata_fields() == [{"key": "contact", "label": "Contact", "type": "email"}]


def test_select_field(settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "tier", "label": "Tier", "type": "select", "options": ["Free", "Paid"]}]
    assert get_team_metadata_fields() == [
        {"key": "tier", "label": "Tier", "type": "select", "options": ["Free", "Paid"]}
    ]


@pytest.mark.parametrize(
    "bad_setting",
    [
        pytest.param("not-a-list", id="not-a-list"),
        pytest.param([{"key": "team_owner"}], id="missing-label"),
        pytest.param([{"key": "", "label": "Team Owner"}], id="empty-key"),
        pytest.param(["not-a-dict"], id="not-a-dict"),
        pytest.param([{"key": "k", "label": "L", "type": "number"}], id="unknown-type"),
        pytest.param([{"key": "k", "label": "L", "type": "select"}], id="select-missing-options"),
        pytest.param([{"key": "k", "label": "L", "type": "select", "options": []}], id="select-empty-options"),
        pytest.param([{"key": "k", "label": "L", "type": "select", "options": ["", "b"]}], id="select-empty-option"),
        pytest.param([{"key": "k", "label": "L", "type": "select", "options": "abc"}], id="select-options-not-list"),
    ],
)
def test_rejects_malformed_setting(settings, bad_setting):
    settings.TEAM_METADATA_FIELDS = bad_setting
    with pytest.raises(ImproperlyConfigured):
        get_team_metadata_fields()
