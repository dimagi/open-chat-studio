import contextlib
import threading
import time
from unittest import mock

import pytest
from langfuse._client.resource_manager import LangfuseResourceManager
from opentelemetry.sdk.trace import TracerProvider

from apps.service_providers.tracing.langfuse import ClientManager


def mock_client_factory():
    """Factory that mints mock clients and registers them with `LangfuseResourceManager`,
    mirroring the real SDK's own `LangfuseResourceManager.__new__`: a public_key already
    present in `_instances` returns the existing entry regardless of the kwargs passed this
    time. This is the exact behavior that makes a bare public_key cache key insufficient --
    the fix under test is `ClientManager` evicting the stale entry itself before a config
    change reaches this factory.
    """

    def mock_register_client(**kwargs):
        public_key = kwargs["public_key"]
        with LangfuseResourceManager._lock:
            if public_key in LangfuseResourceManager._instances:
                return LangfuseResourceManager._instances[public_key]
            client = mock.MagicMock(name=public_key)
            client.sample_rate = kwargs.get("sample_rate")
            LangfuseResourceManager._instances[public_key] = client
            mock_register_client.registry[public_key] = client
        return client

    mock_register_client.registry = {}

    return mock_register_client


@pytest.fixture()
def mock_client_registry():
    return mock_client_factory()


@pytest.fixture()
def langfuse_mock(mock_client_registry):
    """Mock the Langfuse client."""
    with mock.patch("langfuse.Langfuse", side_effect=mock_client_registry) as mock_langfuse:
        yield mock_langfuse


@pytest.fixture()
def client_manager():
    """Return a ClientManager with a short timeout for testing."""
    manager = ClientManager(stale_timeout=0.5)
    yield manager
    manager.shutdown()


@pytest.fixture()
def config():
    """Return a sample config for testing."""
    return {"public_key": "test_key", "secret_key": "test_secret"}


def _hash(client_manager, config):
    return client_manager._config_hash(config)


def _lock_is_free(client_manager) -> bool:
    """Whether another thread could take the manager's lock right now.

    Probed from a second thread because `_lock` is an RLock: the thread that holds it can
    re-acquire it and so cannot tell the difference.
    """
    result = []

    def probe():
        acquired = client_manager._lock.acquire(blocking=False)
        result.append(acquired)
        if acquired:
            client_manager._lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return result[0]


def test_get_creates_new_client(client_manager, config, langfuse_mock, mock_client_registry):
    # Act
    client = client_manager.get(config)

    # Assert
    kwargs = langfuse_mock.call_args.kwargs
    assert isinstance(kwargs.pop("tracer_provider"), TracerProvider)
    assert kwargs == config
    assert len(mock_client_registry.registry) == 1
    assert client == mock_client_registry.registry[config["public_key"]]
    assert len(client_manager._entries) == 1
    assert _hash(client_manager, config) in client_manager._entries


def test_get_reuses_existing_client(client_manager, config, langfuse_mock):
    # Act
    first_client = client_manager.get(config)
    langfuse_mock.reset_mock()

    second_client = client_manager.get(config)

    # Assert
    langfuse_mock.assert_not_called()
    assert first_client is second_client
    assert len(client_manager._entries) == 1


def test_get_creates_different_clients_for_different_configs(client_manager, config, langfuse_mock):
    # Arrange
    other_config = {"public_key": "other_key", "secret_key": "other_secret"}

    # Act
    first_client = client_manager.get(config)
    second_client = client_manager.get(other_config)

    # Assert
    assert first_client != second_client
    assert len(client_manager._entries) == 2


def test_get_rebuilds_the_client_when_the_secret_changes_for_the_same_public_key(client_manager, config, langfuse_mock):
    """A rotated secret for a public_key already cached must build a fresh client, not
    silently keep serving the credentials the client was first built with.
    """
    client_manager.get(config)
    langfuse_mock.reset_mock()

    client_manager.get({**config, "secret_key": "rotated_secret"})

    assert langfuse_mock.call_args.kwargs["secret_key"] == "rotated_secret"
    # Only the current config for this public_key is tracked -- the stale entry was evicted
    # immediately, not left for the next stale-prune pass.
    assert len(client_manager._entries) == 1


def test_get_reuses_the_client_across_sample_rates_for_the_same_public_key(client_manager, config, langfuse_mock):
    """Chatbots sharing a trace provider can each set their own sample rate. The tracer
    samples each trace itself, so the rate must not force a client per rate.
    """
    first_client = client_manager.get({**config, "sample_rate": 0.5})
    langfuse_mock.reset_mock()

    second_client = client_manager.get({**config, "sample_rate": 0.9})

    langfuse_mock.assert_not_called()
    assert first_client is second_client
    first_client.shutdown.assert_not_called()


def test_get_shuts_down_the_stale_sdk_instance_when_the_config_changes(client_manager, config, langfuse_mock):
    """`LangfuseResourceManager` is a singleton keyed by public_key alone -- keying our own
    cache on hash(config) only forces a rebuild if the stale public_key entry it shares with
    the SDK is actually evicted, not just dropped from our own bookkeeping.
    """
    first_client = client_manager.get(config)

    client_manager.get({**config, "secret_key": "rotated_secret"})

    first_client.shutdown.assert_called_once()
    assert LangfuseResourceManager._instances[config["public_key"]] is not first_client


def test_config_change_shuts_the_stale_client_down_without_holding_the_lock(client_manager, config, langfuse_mock):
    """`shutdown()` flushes and can block indefinitely if a consumer thread has died, so
    holding `_lock` across it would stall every other team's `get()`.
    """
    lock_state = []
    first_client = client_manager.get(config)
    first_client.shutdown.side_effect = lambda: lock_state.append(_lock_is_free(client_manager))

    client_manager.get({**config, "secret_key": "rotated_secret"})

    assert lock_state == [True]


def test_prune_shuts_clients_down_without_holding_the_lock(client_manager, config, langfuse_mock):
    lock_state = []
    first_client = client_manager.get(config)
    first_client.shutdown.side_effect = lambda: lock_state.append(_lock_is_free(client_manager))
    client_manager._entries[_hash(client_manager, config)].last_used -= client_manager.stale_timeout + 1

    client_manager._prune_stale()

    assert lock_state == [True]


def test_a_failing_shutdown_does_not_abort_the_prune_pass(client_manager, langfuse_mock):
    """One team's broken client must not leave every later entry in the pass unpruned."""
    configs = [{"public_key": f"key_{i}", "secret_key": f"secret_{i}"} for i in range(3)]
    clients = [client_manager.get(c) for c in configs]
    clients[0].shutdown.side_effect = RuntimeError("flush blew up")
    for c in configs:
        client_manager._entries[_hash(client_manager, c)].last_used -= client_manager.stale_timeout + 1

    client_manager._prune_stale()

    assert client_manager._entries == {}
    for client in clients:
        client.shutdown.assert_called_once()


def test_a_failed_client_build_still_shuts_the_detached_instance_down(client_manager, config, langfuse_mock):
    """A detached instance is out of both caches, so `get()` is the last thing that can stop
    its threads.
    """
    first_client = client_manager.get(config)
    langfuse_mock.side_effect = RuntimeError("can't start new thread")

    with pytest.raises(RuntimeError, match="can't start new thread"):
        client_manager.get({**config, "secret_key": "rotated_secret"})

    first_client.shutdown.assert_called_once()
    assert client_manager._entries == {}


def test_a_config_change_defers_shutdown_of_a_checked_out_client_until_it_is_released(
    client_manager, config, langfuse_mock
):
    with client_manager.checkout(config) as first_client:
        client_manager.get({**config, "secret_key": "rotated_secret"})
        first_client.shutdown.assert_not_called()

    first_client.shutdown.assert_called_once()
    assert LangfuseResourceManager._instances[config["public_key"]] is not first_client


def test_prune_skips_a_checked_out_client(client_manager, config, langfuse_mock):
    client_manager.max_clients = 0

    with client_manager.checkout(config) as client:
        client_manager._entries[_hash(client_manager, config)].last_used -= client_manager.stale_timeout + 1
        client_manager._prune_stale()

    client.shutdown.assert_not_called()
    assert _hash(client_manager, config) in client_manager._entries


def test_prune_stale_clients(client_manager, config, langfuse_mock):
    # Arrange
    other_config = {"public_key": "other_key", "secret_key": "other_secret"}
    first_client = client_manager.get(config)
    client_manager.get(other_config)  # Get second client
    assert len(client_manager._entries) == 2

    # Backdate the first client so only it is past the stale timeout
    client_manager._entries[_hash(client_manager, config)].last_used -= client_manager.stale_timeout + 1

    client_manager._prune_stale()

    assert len(client_manager._entries) == 1
    first_client.shutdown.assert_called_once()


def test_max_clients_limit(client_manager, langfuse_mock, mock_client_registry):
    """Test that the max_clients limit is enforced by removing oldest client."""
    # Arrange
    client_manager.max_clients = 3
    client_manager.stale_timeout = 3600  # Prevent stale pruning from interfering with max_clients pruning

    # Create unique mock clients that return different timestamps
    configs = []

    for i in range(4):
        config = {"public_key": f"key_{i}", "secret_key": f"secret_{i}"}
        configs.append(config)

    # Add clients with increasing timestamps
    clients = []
    for config in configs:
        clients.append(client_manager.get(config))
        time.sleep(0.1)  # Ensure timestamps are different

    assert len(client_manager._entries) == 4

    # Act: add one more client to exceed the limit
    client_manager._prune_stale()

    # Assert: should still have 3 clients but the oldest one should be removed
    assert len(client_manager._entries) == 3
    clients[0].shutdown.assert_called_once()  # The oldest client should be shut down

    # The remaining clients should be the newer ones
    assert _hash(client_manager, configs[0]) not in client_manager._entries
    assert _hash(client_manager, configs[1]) in client_manager._entries
    assert _hash(client_manager, configs[2]) in client_manager._entries
    assert _hash(client_manager, configs[3]) in client_manager._entries


def test_prune_thread_starts_automatically(langfuse_mock):
    """Test that the pruning thread starts automatically when ClientManager is created."""
    with mock.patch("threading.Thread") as thread_mock:
        client_manager = ClientManager()

        # Assert that Thread was created with correct parameters
        thread_mock.assert_called_once()
        _args, kwargs = thread_mock.call_args
        assert kwargs["target"] == client_manager._prune_worker
        assert kwargs["daemon"] is True

        # Assert that thread was started
        thread_mock.return_value.start.assert_called_once()


def test_thread_safety_with_concurrent_access(client_manager, langfuse_mock):
    """Test that the ClientManager is thread-safe with concurrent access."""
    # Arrange
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
    assert len(client_manager._entries) == 10


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
