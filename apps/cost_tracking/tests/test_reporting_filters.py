"""Tests for the cost read path's filtering and attribution rules: the dashboard's
chatbot / platform / participant / tag filters (`CostFilters`) and the ADR-0048
evaluation-source rule. Per-function aggregation behaviour lives in `test_reporting.py`.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.reporting import (
    CostFilters,
    GroupBreakdown,
    cost_summary,
    cost_timeseries,
    cost_total,
    costs_by_experiment,
    coverage_gaps,
    session_usage,
    token_counts,
    usage_by_group,
)
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
# The 30-day window most tests read over, named where a test needs it inside a lambda.
_START = _NOW - timedelta(days=30)


def _usage(team, *, cost, when, **kwargs):
    """Thin wrapper around UsageRecordFactory that coerces `cost` to Decimal
    and forwards optional kwargs (confidence, experiment, session, quantity).
    """
    return UsageRecordFactory.create(team=team, cost=Decimal(str(cost)), at=when, **kwargs)


@pytest.mark.django_db()
class TestCostFilters:
    """The cost read path honours the dashboard's chatbot / participant /
    platform / tag filters. Verified across the public functions.
    """

    def test_cost_summary_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=drop)

        summary = cost_summary(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert summary.total_cost == Decimal("1.00")

    def test_cost_summary_filters_prior_period_too(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=45), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=45), experiment=drop)

        summary = cost_summary(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert summary.previous_period_cost == Decimal("2.00")

    def test_timeseries_filters_by_participant(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        keep = ExperimentSessionFactory.create(experiment=exp, team=team)
        drop = ExperimentSessionFactory.create(experiment=exp, team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, participant=keep.participant)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, participant=drop.participant)

        series = cost_timeseries(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(participant_ids=[keep.participant_id])
        )

        assert [point["chat"] for point in series] == [1.0]

    def test_timeseries_filters_by_platform_via_session(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        web = ExperimentSessionFactory.create(experiment=exp, team=team, platform="web")
        api = ExperimentSessionFactory.create(experiment=exp, team=team, platform="api")
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=web)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, session=api)

        series = cost_timeseries(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(platform_names=["web"])
        )

        assert [point["chat"] for point in series] == [1.0]

    def test_costs_by_experiment_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=drop)

        costs = costs_by_experiment(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert costs == {keep.id: Decimal("1.00000000")}

    def test_coverage_gaps_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), experiment=keep, model_name="keep-model")
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), experiment=drop, model_name="drop-model")

        gaps = coverage_gaps(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert [g.model_name for g in gaps.unpriced] == ["keep-model"]

    def test_tag_filter_narrows_to_entities(self):
        assert CostFilters().narrows_to_entities is False
        assert CostFilters(tag_ids=[1]).narrows_to_entities is True

    def test_cost_summary_filters_by_tag_on_chat(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        tagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        untagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=tagged)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, session=untagged)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal("1.00")

    def test_cost_summary_filters_by_tag_on_message(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        tagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        untagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        message = ChatMessageFactory.create(chat=tagged.chat)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=message)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=tagged)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, session=untagged)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal("1.00")

    def test_tag_filter_excludes_records_without_session(self):
        team = TeamFactory.create()
        tag = TagFactory.create(team=team)
        _usage(team, cost="5.00", when=_NOW - timedelta(days=1))

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_ignores_other_teams_tag_links(self):
        """Filtering by a foreign team's tag id matches nothing: the outer
        queryset is team-scoped, and a generic tag link only references the
        chat it was created for, so it can never point at this team's chats."""
        team = TeamFactory.create()
        other_team = TeamFactory.create()
        other_exp = ExperimentFactory.create(team=other_team)
        other_session = ExperimentSessionFactory.create(team=other_team, experiment=other_exp)
        other_tag = TagFactory.create(team=other_team)
        CustomTaggedItemFactory.create(team=other_team, tag=other_tag, target=other_session.chat)
        _usage(other_team, cost="9.00", when=_NOW - timedelta(days=1), experiment=other_exp, session=other_session)
        exp = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=exp)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=session)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[other_tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_ignores_cross_team_tag_links(self):
        """The inconsistent-link shape: a CustomTaggedItem row carrying a
        FOREIGN team_id whose tag is a local tag and whose object_id targets a
        local chat. The link is not the reading team's, so it must not pull
        the record into a tag-filtered read."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=session.chat)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=session)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_ignores_locally_recorded_link_with_foreign_team_tag_on_chat(self):
        """The mirror inconsistent-link shape: a CustomTaggedItem row with a
        LOCAL team_id, whose tag belongs to a FOREIGN team, targeting the
        session's chat. The tag isn't the reading team's, so it must not
        pull the record into a tag-filtered read."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=exp)
        foreign_tag = TagFactory.create(team=foreign_team)
        CustomTaggedItemFactory.create(team=team, tag=foreign_tag, target=session.chat)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=session)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[foreign_tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_ignores_locally_recorded_link_with_foreign_team_tag_on_message(self):
        """Same mirror shape as above, but the link targets a MESSAGE on the
        session's chat rather than the chat itself - exercises `tag_on_msg`,
        which a chat-targeted tag never reaches."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=exp)
        message = ChatMessageFactory.create(chat=session.chat)
        foreign_tag = TagFactory.create(team=foreign_team)
        CustomTaggedItemFactory.create(team=team, tag=foreign_tag, target=message)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=session)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[foreign_tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_ignores_foreign_team_link_with_local_tag_on_message(self):
        """The remaining inconsistent-link shape: a CustomTaggedItem row with
        a FOREIGN team_id, whose tag IS local, attached to a MESSAGE rather
        than the chat - exercises `tag_on_msg` in `_scoped_records`
        specifically. `test_tag_filter_ignores_cross_team_tag_links` targets
        the chat and never reaches it. The positive control for a local link
        on a message is `test_cost_summary_filters_by_tag_on_message` above."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        message = ChatMessageFactory.create(chat=session.chat)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=message)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=session)

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal(0)

    def test_tag_filter_counts_chat_spend_only(self):
        """A tag-filtered read is per-entity attribution, so evaluation spend on
        the tagged session is excluded (ADR-0048)."""
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        tagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=tagged)
        _usage(
            team,
            cost="0.25",
            when=_NOW - timedelta(days=1),
            experiment=exp,
            session=tagged,
            source=UsageSource.EVALUATION,
        )

        summary = cost_summary(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert summary.total_cost == Decimal("1.00")

    def test_timeseries_filters_by_tag(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        tagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        untagged = ExperimentSessionFactory.create(team=team, experiment=exp)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=tagged)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, session=untagged)

        series = cost_timeseries(team, start=_START, end=_NOW, filters=CostFilters(tag_ids=[tag.id]))

        assert [point["chat"] for point in series] == [1.0]
        assert "evaluation" not in series[0]


@pytest.mark.django_db()
class TestEvaluationSourceRule:
    """ADR-0048: evaluation spend is the team's spend, never a chatbot's, a
    participant's or a conversation's.

    Every case gives the evaluation row an experiment and a session — the shape a
    generation run actually produces — so a read that filtered on those columns being
    null instead of on `source` would fail here.
    """

    @pytest.fixture()
    def spend(self):
        """One chat row and one evaluation row on the same experiment and session."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment)
        when = _NOW - timedelta(days=1)
        _usage(team, cost="1.00", when=when, experiment=experiment, session=session, quantity=100)
        _usage(
            team,
            cost="0.25",
            when=when,
            experiment=experiment,
            session=session,
            quantity=40,
            source=UsageSource.EVALUATION,
        )
        return team, experiment, session

    def test_team_total_counts_both_sources(self, spend):
        team, _, _ = spend

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.total_cost == Decimal("1.25")

    def test_cost_total_counts_both_sources(self, spend):
        team, _, _ = spend

        assert cost_total(team, start=_NOW - timedelta(days=30), end=_NOW).total == Decimal("1.25")

    def test_token_counts_count_both_sources(self, spend):
        team, _, _ = spend

        assert token_counts(team, start=_NOW - timedelta(days=30), end=_NOW).total == 140

    def test_per_experiment_cost_excludes_evaluation(self, spend):
        team, experiment, _ = spend

        costs = costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert costs == {experiment.id: Decimal("1.00000000")}

    def test_session_usage_excludes_evaluation(self, spend):
        _, _, session = spend

        assert session_usage(session).total_cost == Decimal("1.00000000")

    @pytest.mark.parametrize(
        "read",
        [
            pytest.param(
                lambda team, f: cost_summary(team, start=_START, end=_NOW, filters=f).total_cost, id="summary"
            ),
            pytest.param(lambda team, f: cost_total(team, start=_START, end=_NOW, filters=f).total, id="total"),
        ],
    )
    def test_filtering_to_one_chatbot_excludes_evaluation(self, spend, read):
        """A filter narrows the read to an entity just as a grouping does, so it must
        obey the same rule — otherwise the dashboard filtered to one chatbot bills it
        for the judge calls that evaluated it.
        """
        team, experiment, _ = spend

        assert read(team, CostFilters(experiment_ids=[experiment.id])) == Decimal("1.00")

    def test_timeseries_splits_both_sources_when_unfiltered(self, spend):
        team, _, _ = spend

        series = cost_timeseries(team, start=_START, end=_NOW)

        assert [(point["chat"], point["evaluation"]) for point in series] == [(1.0, 0.25)]

    def test_timeseries_filtered_to_one_chatbot_drops_the_evaluation_series(self, spend):
        """Filtering makes it per-entity attribution, so eval spend isn't counted — and the
        chart omits the series rather than showing a zero that reads as "no eval spend"."""
        team, experiment, _ = spend

        series = cost_timeseries(team, start=_START, end=_NOW, filters=CostFilters(experiment_ids=[experiment.id]))

        assert series == [{"date": series[0]["date"], "chat": 1.0}]

    def test_unfiltered_read_stays_a_team_total(self, spend):
        """The flip side: with no filter the same read is a team total, so it counts
        eval spend."""
        team, _, _ = spend

        assert cost_total(team, start=_START, end=_NOW).total == Decimal("1.25")

    def test_usage_by_group_excludes_evaluation(self, spend):
        team, experiment, _ = spend

        rows = usage_by_group(
            team,
            start=_NOW - timedelta(days=30),
            end=_NOW,
            breakdown=GroupBreakdown(field="experiment_id", keys=[experiment.id]),
        )

        assert [(row["key"], row["cost"], row["total"]) for row in rows] == [
            (experiment.id, Decimal("1.00000000"), 100)
        ]
