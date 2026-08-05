import dataclasses
from unittest.mock import MagicMock

import pytest
from django.test import override_settings
from health_check.base import HealthCheck
from health_check.exceptions import ServiceUnavailable

from apps.utils.celery import Queues
from apps.web import views
from apps.web.health_checks import CHECK_SUBSETS, CeleryQueueCheck, _queue_check


def _make_check(ping_result=None, active_queues=None, ping_side_effect=None, active_queues_side_effect=None):
    app = MagicMock()
    if ping_side_effect is not None:
        app.control.ping.side_effect = ping_side_effect
    else:
        app.control.ping.return_value = ping_result
    if active_queues_side_effect is not None:
        app.control.inspect.return_value.active_queues.side_effect = active_queues_side_effect
    else:
        app.control.inspect.return_value.active_queues.return_value = active_queues
    return CeleryQueueCheck(label="chat", queue="celery", app=app)


class TestCeleryQueueCheck:
    @pytest.mark.parametrize(
        "active_queues",
        [
            pytest.param({"worker1": [{"name": "celery"}]}, id="single_worker_on_queue"),
            pytest.param(
                {"worker1": [{"name": "celery"}], "worker2": [{"name": "some-other-queue"}]},
                id="ping_ignores_worker_not_on_queue",
            ),
        ],
    )
    def test_passes_and_scopes_ping_to_workers_on_the_queue(self, active_queues):
        check = _make_check(ping_result=[{"worker1": {"ok": "pong"}}], active_queues=active_queues)

        check.run()

        check.app.control.ping.assert_called_once_with(destination=["worker1"], timeout=1.0)

    @pytest.mark.parametrize(
        ("check_kwargs", "match"),
        [
            pytest.param(
                {"active_queues_side_effect": OSError("boom")},
                "IOError",
                id="active_queues_lookup_raises_oserror",
            ),
            pytest.param(
                {"active_queues": None},
                "No worker for Celery queue",
                id="active_queues_is_empty",
            ),
            pytest.param(
                {
                    "active_queues": {"worker1": [{"name": "some-other-queue"}]},
                    "ping_result": [{"worker1": {"ok": "pong"}}],
                },
                r"No worker for Celery queue 'chat' \(celery\)",
                id="no_worker_consumes_the_queue",
            ),
            pytest.param(
                {"active_queues": {"worker1": [{"name": "celery"}]}, "ping_side_effect": OSError("boom")},
                "IOError",
                id="ping_raises_oserror",
            ),
            pytest.param(
                {"active_queues": {"worker1": [{"name": "celery"}]}, "ping_result": []},
                r"No worker for Celery queue 'chat' \(celery\)",
                id="no_worker_responds_to_ping",
            ),
            pytest.param(
                {
                    "active_queues": {"worker1": [{"name": "celery"}]},
                    "ping_result": [{"worker1": {"unexpected": "value"}}],
                },
                r"No worker for Celery queue 'chat' \(celery\)",
                id="worker_on_queue_ping_response_incorrect",
            ),
        ],
    )
    def test_raises_when_unhealthy(self, check_kwargs, match):
        check = _make_check(**check_kwargs)

        with pytest.raises(ServiceUnavailable, match=match):
            check.run()


class TestCheckSubsets:
    def test_general_subset_has_database_cache_and_redis_checks(self):
        general = CHECK_SUBSETS["general"]

        assert general[0] == "health_check.checks.Database"
        assert general[1] == "health_check.checks.Cache"
        redis_check, redis_kwargs = general[2]
        assert redis_check == "health_check.contrib.redis.Redis"
        assert "client_factory" in redis_kwargs

    def test_celery_subset_has_one_check_per_queue(self):
        celery_subset = CHECK_SUBSETS["celery"]

        assert len(celery_subset) == len(list(Queues))
        assert celery_subset == [_queue_check(queue) for queue in Queues]

    @pytest.mark.parametrize("queue", list(Queues), ids=lambda queue: queue.name)
    def test_per_queue_subset_contains_only_that_queue_check(self, queue):
        subset = CHECK_SUBSETS[f"queue-{queue.name.lower()}"]

        assert subset == [(CeleryQueueCheck, {"label": queue.name.lower(), "queue": queue.value})]

    def test_check_subsets_has_one_entry_per_queue_plus_general_and_celery(self):
        assert set(CHECK_SUBSETS) == {"general", "celery", *(f"queue-{queue.name.lower()}" for queue in Queues)}


@dataclasses.dataclass
class _PassingCheck(HealthCheck):
    async def run(self):
        return None


@dataclasses.dataclass
class _FailingCheck(HealthCheck):
    async def run(self):
        raise ServiceUnavailable("stub failure")


@pytest.fixture()
def fake_subsets(monkeypatch):
    subsets = {
        "general": [_PassingCheck],
        "celery": [_PassingCheck],
        "queue-chat": [_PassingCheck],
        "queue-background": [_FailingCheck],
    }
    monkeypatch.setattr(views, "CHECK_SUBSETS", subsets)
    monkeypatch.setattr(views.HealthCheck, "checks", subsets["general"])
    return subsets


@pytest.mark.django_db()
class TestHealthCheckView:
    def test_default_subset_runs_general_checks(self, client, fake_subsets):
        response = client.get("/status/")

        assert response.status_code == 200

    def test_named_subset_selects_configured_checks(self, client, fake_subsets):
        response = client.get("/status/queue-chat/")

        assert response.status_code == 200

    def test_named_subset_reports_failure_from_its_own_checks(self, client, fake_subsets):
        response = client.get("/status/queue-background/")

        assert response.status_code == 500

    def test_unknown_subset_returns_404(self, client, fake_subsets):
        response = client.get("/status/not-a-real-subset/")

        assert response.status_code == 404

    def test_token_rejected_when_missing(self, client, fake_subsets):
        with override_settings(HEALTH_CHECK_TOKENS=["secret-token"]):
            response = client.get("/status/")

        assert response.status_code == 404

    def test_token_rejected_when_incorrect(self, client, fake_subsets):
        with override_settings(HEALTH_CHECK_TOKENS=["secret-token"]):
            response = client.get("/status/", {"token": "wrong"})

        assert response.status_code == 404

    def test_token_accepted_when_correct(self, client, fake_subsets):
        with override_settings(HEALTH_CHECK_TOKENS=["secret-token"]):
            response = client.get("/status/", {"token": "secret-token"})

        assert response.status_code == 200

    def test_no_token_required_when_none_configured(self, client, fake_subsets):
        with override_settings(HEALTH_CHECK_TOKENS=[]):
            response = client.get("/status/")

        assert response.status_code == 200


@pytest.mark.django_db()
class TestCeleryQueueCheckThroughView:
    """CeleryQueueCheck.run() is synchronous, unlike the async ``run`` used elsewhere in this file.

    HealthCheckView.get is an async view, so it's easy to end up awaiting a sync method directly
    (`await None` raises `TypeError`). The base `HealthCheck.get_result()` guards against this via
    `asyncio.to_thread`, but that guarantee lives in a third-party dependency, so exercise the real
    `CeleryQueueCheck` through the actual view dispatch (not just by calling `run()` in isolation)
    to catch a regression if that dispatch ever changes.
    """

    def _subset(self, ping_result, active_queues):
        app = MagicMock()
        app.control.ping.return_value = ping_result
        app.control.inspect.return_value.active_queues.return_value = active_queues
        return [(CeleryQueueCheck, {"label": "chat", "queue": "celery", "app": app})]

    def test_healthy_queue_returns_200(self, client, monkeypatch):
        subset = self._subset(
            ping_result=[{"worker1": {"ok": "pong"}}],
            active_queues={"worker1": [{"name": "celery"}]},
        )
        monkeypatch.setattr(views, "CHECK_SUBSETS", {"queue-chat": subset})

        response = client.get("/status/queue-chat/")

        assert response.status_code == 200

    def test_unhealthy_queue_returns_500(self, client, monkeypatch):
        subset = self._subset(ping_result=[], active_queues=None)
        monkeypatch.setattr(views, "CHECK_SUBSETS", {"queue-chat": subset})

        response = client.get("/status/queue-chat/")

        assert response.status_code == 500
