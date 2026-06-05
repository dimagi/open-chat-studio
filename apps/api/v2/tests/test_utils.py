"""Unit tests for the shared v2 API helpers."""

from apps.api.v2.utils import as_int, parse_custom_actions


def test_as_int_coerces_and_drops_malformed():
    assert as_int("7") == 7
    assert as_int(7) == 7
    assert as_int("abc") is None
    assert as_int(None) is None
    assert as_int("") is None
    # booleans are not valid ids, even though ``int(True)`` would otherwise coerce to 1
    assert as_int(True) is None
    assert as_int(False) is None


def test_parse_custom_actions_groups_operations_per_action():
    assert parse_custom_actions(["3:weather_get", "3:pollen_get", "5:x"]) == [
        (3, ["weather_get", "pollen_get"]),
        (5, ["x"]),
    ]


def test_parse_custom_actions_handles_scalar_string():
    # A bare string (a malformed param) is a single entry, not an iterable of characters.
    assert parse_custom_actions("12:op_a") == [(12, ["op_a"])]
