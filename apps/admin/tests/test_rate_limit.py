"""Rate limiting behaviour for the admin JSON endpoints."""

from datetime import UTC, datetime

import pytest
import time_machine
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from waffle import get_waffle_flag_model

from apps.admin.views import admin_api_key
from apps.users.models import CustomUser
from apps.utils.rate_limit import RATE_LIMIT_EXEMPT_FLAG

TINY_LIMITS = {"admin_api": {"rate": "2/5m", "fail_open": True}}

# Pinned so the header assertions test the contract rather than whatever
# RATE_LIMIT_ADMIN_API happens to be set to in the developer's .env.
PINNED_LIMITS = {"admin_api": {"rate": "100/5m", "fail_open": True}}

DATE_RANGE = {"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}

ADMIN_API_ENDPOINTS = [
    pytest.param("ocs_admin:teams_api", {}, id="teams"),
    pytest.param("ocs_admin:users_api", {}, id="users"),
    pytest.param("ocs_admin:provider_usage_api", DATE_RANGE, id="provider-usage"),
    pytest.param("ocs_admin:provider_keys_api", DATE_RANGE, id="provider-keys"),
]


# Counting windows are wall-clock aligned (window_start = now - now % window),
# so a test whose requests straddle a boundary would see the counter reset
# mid-run. Pin the clock mid-window so the window never rolls under a test.
MID_WINDOW = datetime(2026, 8, 11, 12, 0, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock():
    with time_machine.travel(MID_WINDOW, tick=False):
        yield


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
@override_settings(RATE_LIMITS=PINNED_LIMITS)
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
    body = response.json()
    assert body["detail"] == "Rate limit exceeded."
    # Deterministic under the pinned clock: 30s into a 300s window leaves 270.
    assert body["available_in"] == 270
    assert response["Retry-After"] == "270"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_the_four_endpoints_share_one_allowance(superuser_client):
    """One scope, one bucket per caller: spending it on one endpoint spends it on all."""
    superuser_client.get(reverse("ocs_admin:teams_api"))
    superuser_client.get(reverse("ocs_admin:users_api"))

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    assert response.status_code == 429


@pytest.mark.django_db()
def test_key_prefers_the_authenticated_user():
    """A staff session is the most specific identity, so it buckets alone."""
    user = CustomUser.objects.create(username="staff@acme.com", is_staff=True)
    request = RequestFactory().get("/")
    request.user = user

    assert admin_api_key(request) == ("user", str(user.pk))


@override_settings(PROVIDER_REPORTING_API_TOKEN="s3cret-token")
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param(
            {"HTTP_AUTHORIZATION": "Bearer s3cret-token"},
            ("reporting_token", "shared"),
            id="valid-token-buckets-on-the-token",
        ),
        pytest.param(
            {"HTTP_AUTHORIZATION": "Bearer wrong"},
            ("ip", "203.0.113.5"),
            id="wrong-token-buckets-on-the-address",
        ),
        pytest.param({}, ("ip", "203.0.113.5"), id="no-credentials-buckets-on-the-address"),
    ],
)
def test_key_falls_back_from_token_to_address(headers, expected):
    """Guessing the reporting token is bounded by the address bucket."""
    request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.5", **headers)
    request.user = AnonymousUser()

    assert admin_api_key(request) == expected


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_anonymous_traffic_cannot_starve_a_staff_user(superuser_client):
    """Probing spends only the address bucket, never a signed-in user's."""
    # A distinct client: the superuser_client fixture force-logs-in the shared one.
    anonymous = Client()
    url = reverse("ocs_admin:teams_api")
    for _ in range(3):
        anonymous.get(url)

    assert anonymous.get(url).status_code == 429
    assert superuser_client.get(url).status_code == 200


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
