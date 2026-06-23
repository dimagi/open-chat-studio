"""Tests for the weekly cost-tracking digest: service aggregation +
Celery task email path."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord
from apps.cost_tracking.services.digest import build_digest
from apps.cost_tracking.tasks import send_unpriced_usage_digest
from apps.utils.factories.team import TeamFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _usage(
    team,
    *,
    when,
    confidence=Confidence.EXACT,
    unit_price="0.00015",
    extra=None,
    model="gpt-4o-mini",
    kind=ServiceKind.LLM_INPUT,
):
    record = UsageRecord.objects.create(
        team=team,
        service_kind=kind,
        provider_type="openai",
        model_name=model,
        quantity=100,
        unit_price=Decimal(unit_price) if unit_price is not None else None,
        cost=Decimal(0),
        confidence=confidence,
        extra=extra or {},
    )
    UsageRecord.objects.filter(pk=record.pk).update(timestamp=when)
    return record


@pytest.mark.django_db()
class TestBuildDigest:
    def test_empty_period_returns_empty_summary(self):
        summary = build_digest(_NOW - timedelta(days=7), _NOW)

        assert summary.is_empty
        assert summary.distinct_unpriced_models == 0
        assert summary.total_unknown_calls == 0

    def test_unpriced_grouped_by_provider_model_kind(self):
        team = TeamFactory.create()
        for _ in range(3):
            _usage(team, when=_NOW - timedelta(days=1), unit_price=None, model="test-unpriced")
        # A priced row in the same window should be ignored.
        _usage(team, when=_NOW - timedelta(days=2), unit_price="0.00250", model="test-priced")

        summary = build_digest(_NOW - timedelta(days=7), _NOW)

        assert len(summary.unpriced_rows) == 1
        row = summary.unpriced_rows[0]
        assert row.model_name == "test-unpriced"
        assert row.calls == 3
        assert summary.distinct_unpriced_models == 1

    def test_unknown_calls_summed_from_extra(self):
        team = TeamFactory.create()
        _usage(
            team,
            when=_NOW - timedelta(days=1),
            confidence=Confidence.UNKNOWN,
            extra={"missing_usage_calls": 4},
            model="test-unknown",
        )
        _usage(
            team,
            when=_NOW - timedelta(days=2),
            confidence=Confidence.UNKNOWN,
            extra={"missing_usage_calls": 2},
            model="test-unknown",
        )

        summary = build_digest(_NOW - timedelta(days=7), _NOW)

        assert len(summary.unknown_rows) == 1
        assert summary.unknown_rows[0].missing_usage_calls == 6
        assert summary.total_unknown_calls == 6

    def test_outside_window_excluded(self):
        team = TeamFactory.create()
        _usage(team, when=_NOW - timedelta(days=30), unit_price=None, model="test-old")

        summary = build_digest(_NOW - timedelta(days=7), _NOW)

        assert summary.is_empty

    def test_cross_team_aggregation(self):
        """The platform digest spans every team. Two teams hitting the same
        unpriced model collapse into one row with combined call count."""
        team_a = TeamFactory.create()
        team_b = TeamFactory.create()
        _usage(team_a, when=_NOW - timedelta(days=1), unit_price=None, model="test-shared")
        _usage(team_b, when=_NOW - timedelta(days=2), unit_price=None, model="test-shared")

        summary = build_digest(_NOW - timedelta(days=7), _NOW)

        assert len(summary.unpriced_rows) == 1
        assert summary.unpriced_rows[0].calls == 2


@pytest.mark.django_db()
class TestSendUnpricedUsageDigest:
    @override_settings(COST_TRACKING_OPERATOR_EMAIL="ops@example.test")
    def test_skips_send_when_empty(self):
        send_unpriced_usage_digest()

        assert mail.outbox == []

    @override_settings(COST_TRACKING_OPERATOR_EMAIL="ops@example.test")
    def test_sends_email_with_subject_and_body(self):
        team = TeamFactory.create()
        # `send_unpriced_usage_digest` uses timezone.now() internally; pin the
        # fixture to "an hour ago" so it sits inside the task's 7-day window.
        _usage(team, when=timezone.now() - timedelta(hours=1), unit_price=None, model="test-fresh-unpriced")

        send_unpriced_usage_digest()

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["ops@example.test"]
        assert "[OCS Cost Tracking]" in message.subject
        assert "test-fresh-unpriced" in message.body

    @override_settings(COST_TRACKING_OPERATOR_EMAIL="")
    def test_falls_back_to_project_contact_email(self, settings):
        team = TeamFactory.create()
        _usage(team, when=timezone.now() - timedelta(hours=1), unit_price=None, model="test-fb-unpriced")
        settings.PROJECT_METADATA = {"CONTACT_EMAIL": "fallback@example.test"}

        send_unpriced_usage_digest()

        assert mail.outbox[0].to == ["fallback@example.test"]
