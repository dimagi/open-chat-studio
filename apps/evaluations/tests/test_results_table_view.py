"""Tests for EvaluationResultTableView: the filter-pill row and the category badges
(neutral, or colored for a two-choice field) added on top of the dynamic
per-evaluator-field columns.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.evaluations.evaluators import EvaluatorResult
from apps.evaluations.export import CategoricalColumn, CategoricalValue
from apps.evaluations.views.evaluation_config_views import ResultFilterPill, _build_result_filter_pills
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory


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
    Response, one per evaluator output field, and Cost - not the full grab-bag of
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
            "Cost",
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

    def test_cost_column_always_present(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _sentiment_result(run, evaluator, sentiment="positive")
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)

        assert "Cost" in list(response.context["table"].columns.columns)

    def test_cost_column_sums_judge_and_generation_for_that_row(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _sentiment_result(run, evaluator, sentiment="positive")
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("1.00"),
            extra={"evaluation_run_id": run.id, "message_id": result.message_id},
        )
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("0.50"),
            extra={"evaluation_run_id": run.id, "message_id": result.message_id},
        )
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert response.status_code == 200
        assert "$1.50" in response.content.decode()


@pytest.mark.django_db()
class TestResultsTableSessionMode:
    """A session-mode dataset's results table swaps Dataset Input/Generated Response for
    a Links column (Session/Message) and a session-history preview - there's no single
    message to show input/output/generated-response for.
    """

    def _build_session_mode_run(self, team):
        session = ExperimentSessionFactory.create(team=team)
        message = EvaluationMessageFactory.create(
            session=session,
            input={},
            output={},
            history=[
                {"message_type": "human", "content": "hi there"},
                {"message_type": "ai", "content": "hello!"},
            ],
        )
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session", messages=[message])
        evaluator = EvaluatorFactory.create(team=team, name="Sentiment Judge")
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team, config=config, evaluator_ids=[evaluator.id])
        output = EvaluatorResult(
            message={
                "input": {},
                "output": {},
                "context": {},
                "history": message.history,
                "metadata": {},
            },
            result={"sentiment": "positive"},
            generated_response="",
        ).model_dump()
        EvaluationResultFactory.create(output=output, team=team, run=run, evaluator=evaluator, message=message)
        return session, run, config

    def test_column_set_swaps_message_columns_for_links_and_preview(self, client, team_with_users):
        session, run, config = self._build_session_mode_run(team_with_users)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        column_names = list(response.context["table"].columns.columns)
        assert column_names == [
            "#",
            "Links",
            "Session Preview",
            f"score ({config.evaluators.first().name})",
            f"sentiment ({config.evaluators.first().name})",
            "Cost",
        ]

    def test_session_chip_enabled_message_chip_disabled(self, client, team_with_users):
        session, run, config = self._build_session_mode_run(team_with_users)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)
        content = response.content.decode()

        session_url = reverse(
            "chatbots:chatbot_session_view",
            args=[team_with_users.slug, session.experiment.public_id, session.external_id],
        )
        assert f'href="{session_url}"' in content
        assert "Session" in content
        assert "Message" in content
        # The Message chip is always disabled for a session-mode row.
        assert 'aria-disabled="true"' in content

    def test_links_column_stops_click_bubbling_to_the_row(self, client, team_with_users):
        """Every row has its own hx-get (opens the detail panel on click, see
        `_row_hx_get_factory`). Without stopping propagation here, clicking a chip in the
        Links column would bubble up and trigger that too, on top of the chip's own
        navigation."""
        session, run, config = self._build_session_mode_run(team_with_users)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)
        content = response.content.decode()

        assert 'onclick="event.stopPropagation()"' in content

    def test_preview_shows_session_history(self, client, team_with_users):
        session, run, config = self._build_session_mode_run(team_with_users)
        client.force_login(team_with_users.members.first())

        url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
        response = client.get(url)

        assert "user: hi there" in response.content.decode()
        assert "assistant: hello!" in response.content.decode()
