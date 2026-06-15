import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Redis state doesn't roll back with the test DB. Clear before each test
    so PricingResolver cache hits don't bleed across tests.
    """
    cache.clear()
    yield
    cache.clear()
