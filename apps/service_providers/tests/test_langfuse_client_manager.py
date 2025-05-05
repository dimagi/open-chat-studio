import contextlib
import threading
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

    time.sleep(0.5)
    client_manager.get(other_config)  # Get second client
    assert len(client_manager.clients) == 2

    # Act
    time.sleep(0.6)  # Sleep longer than the stale timeout
    client_manager._prune_stale()

    # Assert
    assert len(client_manager.clients) == 1
    first_client.shutdown.assert_called_once()


def test_max_clients_limit(client_manager, langfuse_mock):
    """Test that the max_clients limit is enforced by removing oldest client."""
    # Arrange
    client_manager.max_clients = 3

    # Create unique mock clients that return different timestamps
    clients = []
    configs = []

    for i in range(4):
        config = {"public_key": f"key_{i}", "secret_key": f"secret_{i}"}
        configs.append(config)

        client = mock.MagicMock(name=f"client{i}")
        clients.append(client)

    def side_effect(**kwargs):
        for i, c in enumerate(configs):
            if kwargs["public_key"] == c["public_key"]:
                return clients[i]
        return mock.MagicMock()

    langfuse_mock.side_effect = side_effect

    # Add clients with increasing timestamps
    for config in configs:
        client_manager.get(config)
        time.sleep(0.1)  # Ensure timestamps are different

    assert len(client_manager.clients) == 4

    # Act: add one more client to exceed the limit
    client_manager._prune_stale()

    # Assert: should still have 3 clients but the oldest one should be removed
    assert len(client_manager.clients) == 3
    clients[0].shutdown.assert_called_once()  # The oldest client should be shut down

    # The remaining clients should be the newer ones
    remaining_clients = [c[1] for c in client_manager.clients.values()]
    assert clients[1] in remaining_clients
    assert clients[2] in remaining_clients
    assert clients[3] in remaining_clients


def test_prune_thread_starts_automatically(langfuse_mock):
    """Test that the pruning thread starts automatically when ClientManager is created."""
    with mock.patch("threading.Thread") as thread_mock:
        client_manager = ClientManager()

        # Assert that Thread was created with correct parameters
        thread_mock.assert_called_once()
        args, kwargs = thread_mock.call_args
        assert kwargs["target"] == client_manager._prune_worker
        assert kwargs["daemon"] is True

        # Assert that thread was started
        thread_mock.return_value.start.assert_called_once()


def test_thread_safety_with_concurrent_access(langfuse_mock):
    """Test that the ClientManager is thread-safe with concurrent access."""
    # Arrange
    client_manager = ClientManager()
    configs = [{"public_key": f"key_{i}", "secret_key": f"secret_{i}"} for i in range(10)]

    results = []

    def worker(config):
        client = client_manager.get(config)
        results.append(client)

    # Act: access the client manager from multiple threads
    threads = [threading.Thread(target=worker, args=(config,)) for config in configs]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    # Assert
    assert len(results) == 10
    assert len(client_manager.clients) == 10


def test_prune_worker(client_manager, config, langfuse_mock):
    """Test the _prune_worker method with a mocked sleep."""
    # Arrange
    client_manager.prune_interval = 0.01  # Set a very short interval

    # Mock time.sleep to avoid actual sleeping, but allow 3 iterations
    original_sleep = time.sleep
    sleep_called = 0

    def mock_sleep(seconds):
        nonlocal sleep_called
        sleep_called += 1
        if sleep_called <= 3:
            return original_sleep(0.001)  # Very short sleep
        raise InterruptedError("Test complete")  # Stop the loop after 3 iterations

    # Act & Assert
    with mock.patch("time.sleep", side_effect=mock_sleep):
        with mock.patch.object(client_manager, "_prune_stale") as prune_mock:
            with contextlib.suppress(InterruptedError):
                client_manager._prune_worker()

            # Assert _prune_stale was called 3 times
            assert prune_mock.call_count == 3
