import dataclasses
from unittest.mock import MagicMock

import pytest
from django.test import override_settings
from health_check.base import HealthCheck
from health_check.exceptions import ServiceUnavailable

from apps.utils.celery import Queues
from apps.web import views
from apps.web.health_checks import CHECK_SUBSETS, CeleryQueueCheck, _queue_check


def _make_check(ping_result=None, active_queues=None, ping_side_effect=None):
    app = MagicMock()
    if ping_side_effect is not None:
        app.control.ping.side_effect = ping_side_effect
    else:
        app.control.ping.return_value = ping_result
    app.control.inspect.return_value.active_queues.return_value = active_queues
    return CeleryQueueCheck(label="chat", queue="celery", app=app)


class TestCeleryQueueCheck:
    def test_passes_when_worker_consumes_the_queue(self):
        check = _make_check(
            ping_result=[{"worker1": {"ok": "pong"}}],
            active_queues={"worker1": [{"name": "celery"}]},
        )

        check.run()

        check.app.control.inspect.assert_called_once_with(["worker1"])

    def test_raises_when_ping_returns_no_workers(self):
        check = _make_check(ping_result=[])

        with pytest.raises(ServiceUnavailable, match="Celery workers unavailable"):
            check.run()

    def test_raises_when_ping_raises_oserror(self):
        check = _make_check(ping_side_effect=OSError("boom"))

        with pytest.raises(ServiceUnavailable, match="IOError"):
            check.run()

    def test_raises_when_worker_ping_response_is_incorrect(self):
        check = _make_check(ping_result=[{"worker1": {"unexpected": "value"}}])

        with pytest.raises(ServiceUnavailable, match="worker1.*response was incorrect"):
            check.run()

    def test_raises_when_no_worker_consumes_the_queue(self):
        check = _make_check(
            ping_result=[{"worker1": {"ok": "pong"}}],
            active_queues={"worker1": [{"name": "some-other-queue"}]},
        )

        with pytest.raises(ServiceUnavailable, match=r"No worker for Celery queue 'chat' \(celery\)"):
            check.run()

    def test_raises_when_active_queues_is_empty(self):
        check = _make_check(
            ping_result=[{"worker1": {"ok": "pong"}}],
            active_queues=None,
        )

        with pytest.raises(ServiceUnavailable, match="No worker for Celery queue"):
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
