import time
from unittest import mock

import pytest

from apps.service_providers.tracing.langfuse import ClientManager


@pytest.fixture()
def langfuse_mock():
    """Mock the Langfuse client."""
    with mock.patch("langfuse.Langfuse") as mock_langfuse:
        yield mock_langfuse


@pytest.fixture()
def client_manager():
    """Return a ClientManager with a short timeout for testing."""
    return ClientManager(stale_timeout=1)


@pytest.fixture()
def config():
    """Return a sample config for testing."""
    return {"public_key": "test_key", "secret_key": "test_secret"}


def test_get_creates_new_client(client_manager, config, langfuse_mock):
    # Act
    client = client_manager.get(config)

    # Assert
    langfuse_mock.assert_called_with(**config)
    assert client == langfuse_mock.return_value
    assert len(client_manager.clients) == 1


def test_get_reuses_existing_client(client_manager, config, langfuse_mock):
    # Act
    first_client = client_manager.get(config)
    langfuse_mock.reset_mock()

    second_client = client_manager.get(config)

    # Assert
    langfuse_mock.assert_not_called()
    assert first_client == second_client
    assert len(client_manager.clients) == 1


def test_get_creates_different_clients_for_different_configs(client_manager, config, langfuse_mock):
    # Arrange
    other_config = {"public_key": "other_key", "secret_key": "other_secret"}

    # Need different return values for different configs
    def side_effect(**kwargs):
        if kwargs["public_key"] == "test_key":
            return mock.MagicMock(name="client1")
        else:
            return mock.MagicMock(name="client2")

    langfuse_mock.side_effect = side_effect

    # Act
    first_client = client_manager.get(config)
    second_client = client_manager.get(other_config)

    # Assert
    assert first_client != second_client
    assert len(client_manager.clients) == 2


def test_prune_stale_clients(client_manager, config, langfuse_mock):
    # Set up different clients for different configs
    def side_effect(**kwargs):
        if kwargs["public_key"] == "test_key":
            return mock.MagicMock(name="client1")
        else:
            return mock.MagicMock(name="client2")

    langfuse_mock.side_effect = side_effect

    # Arrange
    other_config = {"public_key": "other_key", "secret_key": "other_secret"}
    first_client = client_manager.get(config)
    client_manager.get(other_config)  # Get second client
    assert len(client_manager.clients) == 2

    # Act
    time.sleep(1.1)  # Sleep longer than the stale timeout
    # Get one client again to trigger pruning
    client_manager.get(other_config)

    # Assert
    assert len(client_manager.clients) == 1
    first_client.shutdown.assert_called_once()
