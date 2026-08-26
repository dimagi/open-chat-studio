"""Tests for EvaluationResultTableView: the filter-pill row and the category badges
(neutral, or colored for a two-choice field) added on top of the dynamic
per-evaluator-field columns.
"""

import pytest
from django.urls import reverse

from apps.evaluations.evaluators import EvaluatorResult
from apps.evaluations.export import CategoricalColumn, CategoricalValue
from apps.evaluations.views.evaluation_config_views import ResultFilterPill, _build_result_filter_pills
from apps.teams.models import Flag
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


def _enable_cost_tracking_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()


def _sentiment_result(run, evaluator, *, sentiment):
    output = EvaluatorResult(
        message={
            "input": {"content": "hi", "role": "human"},
            "output": {"content": "hi", "role": "ai"},
            "context": {},
            "history": [],
            "metadata": {},
        },
        result={"sentiment": sentiment},
        generated_response="hi",
    ).model_dump()
    return EvaluationResultFactory.create(output=output, team=run.team, run=run, evaluator=evaluator)


def _acceptability_result(run, evaluator, *, acceptable):
    output = EvaluatorResult(
        message={
            "input": {"content": "hi", "role": "human"},
            "output": {"content": "hi", "role": "ai"},
            "context": {},
            "history": [],
            "metadata": {},
        },
        result={"acceptability": "Acceptable" if acceptable else "Unacceptable"},
        generated_response="hi",
    ).model_dump()
    return EvaluationResultFactory.create(output=output, team=run.team, run=run, evaluator=evaluator)


class TestBuildResultFilterPills:
    def test_bare_labels_for_a_single_field(self):
        columns = [
            CategoricalColumn(
                column_key="sentiment (Judge)",
                field_label="Sentiment",
                values=[
                    CategoricalValue(raw="positive", label="positive"),
                    CategoricalValue(raw="negative", label="negative"),
                ],
            )
        ]

        pills = _build_result_filter_pills(columns, active_field=None, active_value=None)

        assert pills == [
            ResultFilterPill(label="All", field=None, value=None, active=True),
            ResultFilterPill(label="positive", field="sentiment (Judge)", value="positive", active=False),
            ResultFilterPill(label="negative", field="sentiment (Judge)", value="negative", active=False),
        ]

    def test_prefixes_with_field_label_when_more_than_one_field(self):
        columns = [
            CategoricalColumn(column_key="a (X)", field_label="A", values=[CategoricalValue(raw="yes", label="yes")]),
            CategoricalColumn(column_key="b (X)", field_label="B", values=[CategoricalValue(raw="no", label="no")]),
        ]

        pills = _build_result_filter_pills(columns, active_field=None, active_value=None)

        assert [pill.label for pill in pills] == ["All", "A: yes", "B: no"]

    def test_marks_the_matching_pill_active(self):
        columns = [
            CategoricalColumn(
                column_key="a (X)",
                field_label="A",
                values=[CategoricalValue(raw="yes", label="yes"), CategoricalValue(raw="no", label="no")],
            )
        ]

        pills = _build_result_filter_pills(columns, active_field="a (X)", active_value="yes")

        active = [pill for pill in pills if pill.active]
        assert [pill.label for pill in active] == ["yes"]

    def test_no_categorical_columns_yields_only_all(self):
        assert _build_result_filter_pills([], active_field=None, active_value=None) == [
            ResultFilterPill(label="All", field=None, value=None, active=True)
        ]


@pytest.mark.django_db()
class TestResultsTableFilteringAndBadges:
    def test_categorical_value_renders_as_a_neutral_badge(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert '<span class="badge badge-outline badge-sm">' in content
        assert "positive" in content

    def test_two_choice_field_renders_colored_badges(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(
            team=team_with_users,
            name="Acceptability Judge",
            params={
                "llm_prompt": "x",
                "output_schema": {
                    "acceptability": {
                        "type": "choice",
                        "description": "x",
                        "choices": ["Acceptable", "Unacceptable"],
                    },
                },
            },
        )
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _acceptability_result(run, evaluator, acceptable=True)
        _acceptability_result(run, evaluator, acceptable=False)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert '<span class="badge badge-success">Acceptable</span>' in content
        assert '<span class="badge badge-error">Unacceptable</span>' in content

    def test_filter_pills_list_every_distinct_value(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        content = response.content.decode()
        for label in ("All", "positive", "neutral", "negative"):
            assert label in content

    def test_filtering_by_value_narrows_the_table_to_matching_rows(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        _sentiment_result(run, evaluator, sentiment="negative")
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url, {"filter_field": f"sentiment ({evaluator.name})", "filter_value": "positive"})

        assert response.status_code == 200
        assert len(response.context["table"].rows) == 1

    def test_unknown_filter_field_is_ignored_rather_than_dropping_every_row(self, client, team_with_users):
        """A crafted/stale filter_field that isn't one of this run's categorical columns
        must not silently zero out the table - it's treated the same as no filter."""
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url, {"filter_field": "not-a-real-column", "filter_value": "x"})

        assert len(response.context["table"].rows) == 1

    def test_no_categorical_fields_hides_the_filter_row(self, client, team_with_users):
        """A run with no choice/binary evaluator fields (e.g. Python evaluators only) has
        nothing to filter by, so the pill row - which would just be a lone "All" - stays hidden."""
        evaluator = EvaluatorFactory.create(
            team=team_with_users,
            type="PythonEvaluator",
            params={"code": "def main(**kwargs): return {'notes': 'ok'}"},
        )
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        EvaluationResultFactory.create(
            output={"result": {"notes": "ok"}}, team=team_with_users, run=run, evaluator=evaluator
        )
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.status_code == 200
        assert "All" not in response.content.decode()


@pytest.mark.django_db()
class TestResultsTableCuratedColumns:
    """The results table shows a fixed set of columns - #, Dataset Input, Generated
    Response, one per evaluator output field, and Tokens - not the full grab-bag of
    context/tag/session-link columns build_evaluation_table_data also produces.
    """

    def test_dataset_output_and_session_links_are_not_columns(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        # The default factory schema also has a "score" (int) field - it gets a plain
        # column (not a badge), confirming non-categorical dynamic fields are still
        # curated in, just without special rendering.
        column_names = list(response.context["table"].columns.columns)
        assert column_names == [
            "#",
            "Dataset Input",
            "Generated Response",
            f"score ({evaluator.name})",
            f"sentiment ({evaluator.name})",
        ]

    def test_long_free_text_fields_are_clamped_not_left_to_blow_out_row_height(self, client, team_with_users):
        """Dataset Input, Generated Response, and free-text evaluator fields (e.g. this
        run's "score") are all attacker-length-controlled/model-generated text that can run
        arbitrarily long - each gets wrapped in the line-clamp so one long row doesn't
        expand every row in the table. `title` carries the untruncated text for a hover
        tooltip since the clamp itself hides it."""
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        long_text = "word " * 200
        output = EvaluatorResult(
            message={
                "input": {"content": long_text, "role": "human"},
                "output": {"content": "hi", "role": "ai"},
                "context": {},
                "history": [],
                "metadata": {},
            },
            result={"sentiment": "positive", "score": 1},
            generated_response=long_text,
        ).model_dump()
        EvaluationResultFactory.create(output=output, team=team_with_users, run=run, evaluator=evaluator)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        content = response.content.decode()
        assert content.count(f'line-clamp-2 max-w-md" title="{long_text}"') == 2

    def test_tokens_column_present_only_when_cost_tracking_enabled(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)
        assert "Tokens" not in list(response.context["table"].columns.columns)

        _enable_cost_tracking_for(team_with_users)

        response = client.get(url)
        assert "Tokens" in list(response.context["table"].columns.columns)

    def test_tokens_column_sums_judge_and_generation_for_that_row(self, client, team_with_users):
        _enable_cost_tracking_for(team_with_users)

        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _sentiment_result(run, evaluator, sentiment="positive")
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            quantity=100,
            extra={"evaluation_run_id": run.id, "message_id": result.message_id},
        )
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            quantity=50,
            extra={"evaluation_run_id": run.id, "message_id": result.message_id},
        )
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.status_code == 200
        assert "150" in response.content.decode()
