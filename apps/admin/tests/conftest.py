import pytest

from apps.users.models import CustomUser
from apps.utils.tests.elevation import elevate_session
from apps.web.elevation import Grant


@pytest.fixture()
def superuser_client(client):
    """A logged-in superuser holding the OCS admin elevation, which is what the admin views
    and the cross-team reporting APIs all require."""
    user = CustomUser.objects.create(username="admin@acme.com", is_staff=True, is_superuser=True)
    client.force_login(user)
    elevate_session(client, Grant.OCS_ADMIN)
    return client


@pytest.fixture()
def staff_client(client):
    """Staff, elevated: enough for the dashboards, not for the superuser-only views."""
    staff = CustomUser.objects.create(username="staff@acme.com", is_staff=True)
    client.force_login(staff)
    elevate_session(client, Grant.OCS_ADMIN)
    return client
