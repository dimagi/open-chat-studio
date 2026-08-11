"""Rate limiting behaviour for the admin JSON endpoints."""

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse
from waffle import get_waffle_flag_model

from apps.users.models import CustomUser
from apps.utils.rate_limit import RATE_LIMIT_EXEMPT_FLAG

TINY_LIMITS = {"admin_api": {"rate": "2/5m", "fail_open": True}}

DATE_RANGE = {"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}

ADMIN_API_ENDPOINTS = [
    pytest.param("ocs_admin:teams_api", {}, id="teams"),
    pytest.param("ocs_admin:users_api", {}, id="users"),
    pytest.param("ocs_admin:provider_usage_api", DATE_RANGE, id="provider-usage"),
    pytest.param("ocs_admin:provider_keys_api", DATE_RANGE, id="provider-keys"),
]


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()  # Clear all waffle and other caches


@pytest.fixture()
def superuser_client(client):
    user = CustomUser.objects.create(username="admin@acme.com", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.mark.django_db()
@pytest.mark.parametrize(("url_name", "params"), ADMIN_API_ENDPOINTS)
def test_endpoints_report_their_allowance(superuser_client, url_name, params):
    """Every admin JSON endpoint carries the shared rate limit headers."""
    response = superuser_client.get(reverse(url_name), params)

    assert response["X-RateLimit-Limit"] == "100"
    assert response["X-RateLimit-Remaining"] == "99"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.parametrize(("url_name", "params"), ADMIN_API_ENDPOINTS)
def test_over_limit_requests_are_rejected_when_enforcing(superuser_client, url_name, params):
    """A third request inside the window is refused with the shared 429 contract."""
    url = reverse(url_name)
    superuser_client.get(url, params)
    superuser_client.get(url, params)

    response = superuser_client.get(url, params)

    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded.", "available_in": pytest.approx(300, abs=300)}
    assert int(response["Retry-After"]) > 0


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_unauthenticated_requests_are_counted(client):
    """Counting precedes the auth check, so probing consumes the caller's allowance."""
    url = reverse("ocs_admin:users_api")
    assert client.get(url).status_code != 429
    assert client.get(url).status_code != 429

    assert client.get(url).status_code == 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_serves_over_limit_requests(superuser_client):
    """The shipped default records the crossing without blocking."""
    url = reverse("ocs_admin:teams_api")
    for _ in range(4):
        response = superuser_client.get(url)

    assert response.status_code == 200
    assert response["X-RateLimit-Remaining"] == "0"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_exempt_requests_are_never_counted(superuser_client):
    """The kill switch bypasses this scope like every other."""
    # update_or_create, not create: a data migration pre-creates this flag row.
    get_waffle_flag_model().objects.update_or_create(name=RATE_LIMIT_EXEMPT_FLAG, defaults={"everyone": True})
    url = reverse("ocs_admin:teams_api")

    for _ in range(5):
        response = superuser_client.get(url)

    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response.headers
