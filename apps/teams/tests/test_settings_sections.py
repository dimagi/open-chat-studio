import pytest

from apps.teams import settings_sections
from apps.teams.settings_sections import SettingsSection, register_settings_section, with_registered_sections


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    monkeypatch.setattr(settings_sections, "_registered", {})


def _section(key):
    return SettingsSection(key=key, label=key, icon="fa-circle", template="section.html")


def _keys(sections):
    return [section.key for section in sections]


def test_registered_section_is_placed_after_its_anchor():
    register_settings_section(section=_section("extra"), after="first")

    sections = with_registered_sections([_section("first"), _section("second")])

    assert _keys(sections) == ["first", "extra", "second"]


def test_registering_the_same_key_twice_adds_it_once():
    register_settings_section(section=_section("extra"), after="first")
    register_settings_section(section=_section("extra"), after="first")

    sections = with_registered_sections([_section("first")])

    assert _keys(sections) == ["first", "extra"]


def test_unknown_anchor_names_the_missing_key():
    register_settings_section(section=_section("extra"), after="missing")

    with pytest.raises(ValueError, match="'missing'"):
        with_registered_sections([_section("first")])
