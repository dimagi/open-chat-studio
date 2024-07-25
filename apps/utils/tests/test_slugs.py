from unittest.mock import Mock, patch

import pytest

from ..slug import get_next_slug, get_next_unique_slug


def test_next_slug_basic():
    assert "slug-11" == get_next_slug("slug", 11)


def test_next_slug_truncate():
    assert "slug-11" == get_next_slug("slug", 11, max_length=7)
    assert "slu-11" == get_next_slug("slug", 11, max_length=6)
    assert "slu-100" == get_next_slug("slug", 100, max_length=7)
    assert "sl-100" == get_next_slug("slug", 100, max_length=6)


def test_next_slug_fail():
    with pytest.raises(ValueError, match="Suffix 11111 is too long to create a unique slug!"):
        get_next_slug("slug", 11111, max_length=6)


def test_get_next_unique_slug():
    with patch("apps.utils.slug._instance_exists", side_effect=[True, True, False]) as exists:
        slug = get_next_unique_slug(Mock(), "test abc", field_name="slug")
    assert slug == "test-abc-3"
    assert exists.call_count == 3
