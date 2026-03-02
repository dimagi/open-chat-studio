from unittest.mock import Mock, patch

import pytest

from ..slug import get_next_slug, get_next_unique_id, get_next_unique_slug


def test_next_slug_basic():
    assert get_next_slug("slug", 11) == "slug-11"


def test_next_slug_truncate():
    assert get_next_slug("slug", 11, max_length=7) == "slug-11"
    assert get_next_slug("slug", 11, max_length=6) == "slu-11"
    assert get_next_slug("slug", 100, max_length=7) == "slu-100"
    assert get_next_slug("slug", 100, max_length=6) == "sl-100"


def test_next_slug_fail():
    with pytest.raises(ValueError, match="Suffix 11111 is too long to create a unique slug!"):
        get_next_slug("slug", 11111, max_length=6)


def test_get_next_unique_slug():
    with patch("apps.utils.slug._instance_exists", side_effect=[True, True, False]) as exists:
        slug = get_next_unique_slug(Mock(), "test abc", field_name="slug")  # ty: ignore[invalid-argument-type]
    assert slug == "test-abc-3"
    assert exists.call_count == 3


def test_get_next_unique_id():
    with patch("apps.utils.slug._instance_exists", side_effect=[True, True, False]) as exists:
        hash_id = get_next_unique_id(Mock(), ["test abc", 1, 3], field_name="id", length=5)  # ty: ignore[invalid-argument-type]
    assert len(hash_id) == 5
    assert exists.call_count == 3
