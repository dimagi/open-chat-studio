import pytest
from django.test import SimpleTestCase

from ..slug import get_next_slug


class NextSlugTest(SimpleTestCase):
    def test_next_slug_basic(self):
        assert "slug-11" == get_next_slug("slug", 11)

    def test_next_slug_truncate(self):
        assert "slug-11" == get_next_slug("slug", 11, max_length=7)
        assert "slu-11" == get_next_slug("slug", 11, max_length=6)
        assert "slu-100" == get_next_slug("slug", 100, max_length=7)
        assert "sl-100" == get_next_slug("slug", 100, max_length=6)

    def test_next_slug_fail(self):
        with pytest.raises(ValueError, match="Suffix 11111 is too long to create a unique slug!"):
            get_next_slug("slug", 11111, max_length=6)
