from unittest import mock

import pytest
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.transport import Transport

from apps.events.tasks import fire_scheduled_trigger
from apps.utils.celery import delay_in_new_trace
from config.celery import app


class _NullTransport(Transport):
    def capture_envelope(self, envelope):
        pass


class _Enqueued(Exception):
    pass


@pytest.fixture()
def sent_headers():
    """Headers of each message enqueued through Sentry's real Celery wrappers, stopped before the broker."""
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_NullTransport(),
        integrations=[CeleryIntegration()],
        default_integrations=False,
        traces_sample_rate=1.0,
    )
    headers = []

    def capture(*args, **kwargs):
        headers.append(kwargs["headers"])
        raise _Enqueued

    with mock.patch.object(app.amqp, "create_task_message", side_effect=capture):
        yield headers
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def test_task_enqueued_from_an_unsampled_trace_does_not_inherit_it(sent_headers):
    with sentry_sdk.start_transaction(name="poller", sampled=False) as transaction:
        with pytest.raises(_Enqueued):
            fire_scheduled_trigger.delay(1)
        with pytest.raises(_Enqueued):
            delay_in_new_trace(fire_scheduled_trigger, 1)

    inherited, new = sent_headers
    assert inherited["sentry-trace"].startswith(transaction.trace_id)
    assert inherited["sentry-trace"].endswith("-0")
    assert "sentry-trace" not in new
    assert "baggage" not in new
