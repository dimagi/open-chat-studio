import pytest

from apps.users.models import CustomUser


@pytest.fixture()
def superuser_client(client):
    """A logged-in superuser, which is what the staff-only admin views and the
    cross-team reporting APIs all require."""
    user = CustomUser.objects.create(username="admin@acme.com", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client
