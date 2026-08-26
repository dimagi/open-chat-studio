import re
from decimal import Decimal
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind
from apps.generics.actions import CHIP_BUTTON_STYLE
from apps.trace.models import TraceStatus
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


def _make_trace(team, **kwargs):
    session = ExperimentSessionFactory.create(team=team)
    return TraceFactory.create(
        team=team,
        experiment=session.experiment,
        session=session,
        participant=session.participant,
        status=TraceStatus.SUCCESS,
        duration=1000,
        **kwargs,
    )


@pytest.mark.django_db()
def test_trace_detail_view_renders_filter_links(client, team_with_users):
    """The trace detail page links to the trace table pre-filtered by session/chatbot/participant."""
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    home_url = reverse("trace:home", args=[team.slug])

    # Collect the filter query params from each link pointing at the trace table home.
    links = {}
    for href in re.findall(rf'href="({re.escape(home_url)}\?[^"]+)"', content):
        params = parse_qs(urlparse(unescape(href)).query)
        column = next(key[2:] for key in params if key.startswith("f_"))
        links[column] = params

    session_link = links["session_id"]
    assert session_link["op_session_id"] == ["equals"]
    assert session_link["f_session_id"] == [str(trace.session.external_id)]

    experiment_link = links["experiment"]
    assert experiment_link["op_experiment"] == ["any of"]
    assert experiment_link["f_experiment"] == [str(trace.experiment_id)]

    participant_link = links["participant"]
    assert participant_link["op_participant"] == ["equals"]
    assert participant_link["f_participant"] == [trace.participant.identifier]


@pytest.mark.django_db()
def test_trace_detail_view_reads_tokens_from_usage_records(client, team_with_users):
    """The token card is fed by UsageRecord rows for the trace, not by counters on the row."""
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)
    UsageRecordFactory.create(
        team=team, trace=trace, service_kind=ServiceKind.LLM_INPUT, model_name="gpt-4o", quantity=1000
    )
    UsageRecordFactory.create(
        team=team, trace=trace, service_kind=ServiceKind.LLM_CACHED_INPUT, model_name="gpt-4o", quantity=200
    )
    UsageRecordFactory.create(
        team=team, trace=trace, service_kind=ServiceKind.LLM_OUTPUT, model_name="gpt-4o", quantity=500
    )
    UsageRecordFactory.create(team=team, trace=_make_trace(team), service_kind=ServiceKind.LLM_INPUT, quantity=999)

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    usage = response.context_data["token_usage"]
    assert (usage.input_tokens, usage.output_tokens, usage.total) == (1200, 500, 1700)
    content = response.content.decode()
    assert "1,700" in content
    assert "gpt-4o" in content
    assert "200 cached" in content


@pytest.mark.django_db()
def test_trace_detail_view_without_usage_records(client, team_with_users):
    """No recorded usage renders the card's em dash rather than a zero total."""
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    assert response.context_data["token_usage"].by_model == []


@pytest.mark.django_db()
def test_trace_detail_view_shows_total_cost(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)
    rule = PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-priced-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price="0.00015",
    )
    UsageRecordFactory.create(
        team=team,
        trace=trace,
        model_name="test-priced-model",
        service_kind=ServiceKind.LLM_INPUT,
        quantity=100,
        cost=Decimal("1.50"),
        pricing_rule=rule,
    )

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    assert response.context_data["token_usage"].total_cost == Decimal("1.50000000")
    assert b'data-testid="trace-cost"' in response.content
    assert "1.50" in response.content.decode()


@pytest.mark.django_db()
def test_trace_detail_view_shows_zero_cost_for_fully_priced_model(client, team_with_users):
    """A fully priced model (pricing_rule set) that happens to cost $0 must render "$0.00" in
    its per-model row, not fall through to blank because `model.cost` is a falsy Decimal 0."""
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)
    rule = PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-priced-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price="0.00015",
    )
    UsageRecordFactory.create(
        team=team,
        trace=trace,
        model_name="test-priced-model",
        service_kind=ServiceKind.LLM_INPUT,
        quantity=100,
        cost=Decimal("0"),
        pricing_rule=rule,
    )

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "$0.00" in content
    assert "unpriced" not in content


@pytest.mark.django_db()
def test_trace_detail_view_shows_no_pricing_data_when_unpriced(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)
    UsageRecordFactory.create(
        team=team, trace=trace, service_kind=ServiceKind.LLM_INPUT, quantity=100, cost=Decimal("0")
    )

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    assert "No pricing data" in response.content.decode()


@pytest.mark.django_db()
def test_trace_detail_view_shows_confidence_badge_for_estimated_rows(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)
    UsageRecordFactory.create(
        team=team,
        trace=trace,
        service_kind=ServiceKind.LLM_INPUT,
        quantity=100,
        confidence=Confidence.ESTIMATED,
    )

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    assert b'data-testid="trace-confidence-badge"' in response.content
    assert "Estimated" in response.content.decode()


@pytest.mark.django_db()
def test_trace_table_view_filters_by_team(client, team_with_users):
    """The trace list view must return only traces belonging to the requesting team."""
    team = team_with_users
    user = team.members.first()
    other_team = TeamFactory.create()

    own_trace = _make_trace(team)
    foreign_trace = _make_trace(other_team)

    client.force_login(user)
    response = client.get(reverse("trace:table", args=[team.slug]))

    assert response.status_code == 200
    visible_ids = {row.record.id for row in response.context_data["table"].rows}
    assert own_trace.id in visible_ids
    assert foreign_trace.id not in visible_ids


@pytest.mark.django_db()
def test_trace_table_renders_chips_like_other_tables(client, team_with_users):
    """The bot and session columns stand in for the record, so they carry the shared chip styling
    and the same one-line truncation as the participant chips in the session tables."""
    team = team_with_users
    user = team.members.first()
    _make_trace(team)

    client.force_login(user)
    response = client.get(reverse("trace:table", args=[team.slug]))

    chips = re.findall(r'<a [^>]*class="([^"]*)"', response.content.decode())
    assert chips
    for classes in chips:
        assert CHIP_BUTTON_STYLE in classes
        assert "max-w-xs" in classes
    assert response.content.decode().count("min-w-0 truncate") == 2
