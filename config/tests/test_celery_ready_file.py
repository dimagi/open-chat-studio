"""Tests for the Celery worker readiness file.

Guards against the regression in which the ECS health check for the worker containers used
``celery inspect ping``: it cost a second full app import per check, exceeded the container health
check timeout on the workers' 0.5 vCPU, and tripped the deployment circuit breaker (see
config/celery.py).
"""

import pathlib

import pytest

from config import celery as celery_config


@pytest.fixture()
def ready_file(tmp_path, monkeypatch):
    path = tmp_path / "celery-ready"
    monkeypatch.setattr(celery_config, "READY_FILE", str(path))
    return path


def test_ready_file_written_when_worker_is_ready(ready_file):
    celery_config.on_worker_ready()

    assert ready_file.exists()


def test_ready_file_removed_on_shutdown(ready_file):
    ready_file.touch()

    celery_config.on_worker_shutdown()

    assert not ready_file.exists()


def test_shutdown_tolerates_a_missing_ready_file(ready_file):
    celery_config.on_worker_shutdown()

    assert not ready_file.exists()


@pytest.mark.parametrize("handler", [celery_config.on_worker_ready, celery_config.on_worker_shutdown])
def test_no_file_written_when_unconfigured(handler, tmp_path, monkeypatch):
    """The file is an ECS affordance; nothing should appear in local dev or a self-hosted install."""
    monkeypatch.setattr(celery_config, "READY_FILE", None)
    monkeypatch.chdir(tmp_path)

    handler()

    assert list(pathlib.Path(tmp_path).iterdir()) == []
