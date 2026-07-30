import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Redis state doesn't roll back with the test DB, so a PricingRule seeded by one
    test stays in the resolver cache for the next one — which then writes a
    UsageRecord pointing at a rolled-back rule id. Mirrors the cost_tracking fixture.
    """
    cache.clear()
    yield
    cache.clear()
