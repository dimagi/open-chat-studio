import pytest

from apps.evaluations.aggregation import compute_aggregates_for_run
from apps.evaluations.aggregators import aggregate_field, get_aggregators_for_value
from apps.evaluations.models import EvaluationRun, EvaluationRunStatus
from apps.evaluations.utils import build_trend_data
from apps.utils.factories.evaluations import EvaluationResultFactory, EvaluationRunFactory, EvaluatorFactory


class TestAggregators:
    def test_get_aggregators_for_numeric(self):
        aggregators = get_aggregators_for_value(1.5)
        names = {a.name for a in aggregators}
        assert names == {"mean", "median", "min", "max", "std_dev"}

    def test_get_aggregators_for_string(self):
        aggregators = get_aggregators_for_value("positive")
        names = {a.name for a in aggregators}
        assert names == {"distribution", "mode"}

    def test_get_aggregators_for_bool(self):
        aggregators = get_aggregators_for_value(True)
        names = {a.name for a in aggregators}
        assert names == {"distribution", "mode"}

    def test_aggregate_numeric_field(self):
        result = aggregate_field([1, 2, 3, 4, 5])
        assert result["type"] == "numeric"
        assert result["count"] == 5
        assert result["mean"] == 3.0
        assert result["median"] == 3
        assert result["min"] == 1
        assert result["max"] == 5

    def test_aggregate_categorical_field(self):
        result = aggregate_field(["good", "good", "bad", "good"])
        assert result["type"] == "categorical"
        assert result["count"] == 4
        assert result["mode"] == "good"
        assert result["distribution"] == {"good": 75.0, "bad": 25.0}

    def test_aggregate_empty_returns_empty(self):
        assert aggregate_field([]) == {}

    def test_aggregate_filters_none_values(self):
        result = aggregate_field([None, 1, 2, None, 3])
        assert result["type"] == "numeric"
        assert result["count"] == 3  # None values filtered out
        assert result["mean"] == 2.0

    def test_aggregate_all_none_returns_empty(self):
        assert aggregate_field([None, None]) == {}

    def test_aggregate_mixed_types(self):
        # First value determines type; mismatched types are filtered out
        result = aggregate_field([1, "2", 3])
        assert result["type"] == "numeric"
        assert result["count"] == 2  # "2" filtered out
        assert result["mean"] == 2.0


@pytest.mark.django_db()
class TestComputeAggregatesForRun:
    def test_computes_aggregates_from_results(self):
        run: EvaluationRun = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        evaluator = EvaluatorFactory(team=run.team)

        for score in [0.8, 0.9, 0.7]:
            EvaluationResultFactory(
                run=run,
                evaluator=evaluator,
                team=run.team,
                output={"result": {"score": score, "label": "good"}},
            )

        aggregates = compute_aggregates_for_run(run)

        assert len(aggregates) == 1
        agg = aggregates[0]
        assert agg.evaluator == evaluator
        assert agg.aggregates["score"]["type"] == "numeric"
        assert agg.aggregates["score"]["mean"] == 0.8
        assert agg.aggregates["label"]["type"] == "categorical"
        assert agg.aggregates["label"]["mode"] == "good"

    def test_skips_results_with_errors(self):
        run: EvaluationRun = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        evaluator = EvaluatorFactory(team=run.team)

        EvaluationResultFactory(run=run, evaluator=evaluator, team=run.team, output={"result": {"score": 0.5}})
        EvaluationResultFactory(run=run, evaluator=evaluator, team=run.team, output={"error": "failed"})

        aggregates = compute_aggregates_for_run(run)
        assert aggregates[0].aggregates["score"]["count"] == 1


@pytest.mark.django_db()
class TestBuildTrendData:
    def test_builds_trend_from_runs(self):
        run = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)
        evaluator = EvaluatorFactory(team=run.team, name="Quality Check")

        run.aggregates.create(
            evaluator=evaluator,
            aggregates={
                "score": {"type": "numeric", "mean": 0.85, "count": 10},
                "rating": {"type": "categorical", "mode": "good", "distribution": {"good": 80, "bad": 20}, "count": 10},
            },
        )

        trend_data = build_trend_data([run])

        assert "Quality Check" in trend_data
        assert trend_data["Quality Check"]["score (numeric)"]["type"] == "numeric"
        assert trend_data["Quality Check"]["score (numeric)"]["points"][0]["value"] == 0.85
        assert trend_data["Quality Check"]["rating (categorical)"]["type"] == "categorical"
        assert trend_data["Quality Check"]["rating (categorical)"]["categories"] == ["bad", "good"]

    def test_empty_runs_returns_empty(self):
        assert build_trend_data([]) == {}

    def test_respects_use_in_aggregations_setting(self):
        run = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)
        evaluator = EvaluatorFactory(
            team=run.team,
            name="Test Evaluator",
            params={
                "output_schema": {
                    "score": {"type": "float", "description": "Score", "use_in_aggregations": True},
                    "internal": {"type": "float", "description": "Internal", "use_in_aggregations": False},
                }
            },
        )

        run.aggregates.create(
            evaluator=evaluator,
            aggregates={
                "score": {"type": "numeric", "mean": 0.85, "count": 10},
                "internal": {"type": "numeric", "mean": 0.5, "count": 10},
            },
        )

        trend_data = build_trend_data([run])

        assert "score (numeric)" in trend_data["Test Evaluator"]
        assert "internal (numeric)" not in trend_data["Test Evaluator"]

    def test_handles_field_type_change_categorical_to_numeric(self):
        """Test that changing a field from categorical to numeric creates separate trend entries.

        Previously this caused a TypeError because mixed values were summed together.
        Now each type gets its own trend entry keyed by (field_name, type).
        """
        evaluator = EvaluatorFactory(name="Quality Check")
        team = evaluator.team

        # First run: field "rating" was categorical (e.g., "good"/"bad")
        run1: EvaluationRun = EvaluationRunFactory(team=team, status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        for rating in ["good", "good", "bad", "good"]:
            EvaluationResultFactory(
                run=run1,
                evaluator=evaluator,
                team=team,
                output={"result": {"rating": rating}},
            )
        compute_aggregates_for_run(run1)

        # Second run: field "rating" is now numeric (e.g., 1-5 scale)
        run2: EvaluationRun = EvaluationRunFactory(team=team, status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        for rating in [4, 5, 4, 3, 5]:
            EvaluationResultFactory(
                run=run2,
                evaluator=evaluator,
                team=team,
                output={"result": {"rating": rating}},
            )
        compute_aggregates_for_run(run2)

        trend_data = build_trend_data([run1, run2])

        # Should have separate entries for each type
        assert "rating (categorical)" in trend_data["Quality Check"]
        assert "rating (numeric)" in trend_data["Quality Check"]

        # Categorical entry should only have categorical data
        categorical_data = trend_data["Quality Check"]["rating (categorical)"]
        assert categorical_data["type"] == "categorical"
        assert len(categorical_data["points"]) == 1
        assert categorical_data["points"][0]["value"] == "good"

        # Numeric entry should only have numeric data
        numeric_data = trend_data["Quality Check"]["rating (numeric)"]
        assert numeric_data["type"] == "numeric"
        assert len(numeric_data["points"]) == 1
        assert numeric_data["points"][0]["value"] == 4.2

    def test_handles_field_type_change_numeric_to_categorical(self):
        """Test that changing a field from numeric to categorical creates separate trend entries.

        Previously this caused data corruption with mixed types in points.
        Now each type gets its own trend entry keyed by (field_name, type).
        """
        evaluator = EvaluatorFactory(name="Quality Check")
        team = evaluator.team

        # First run: field "rating" was numeric (e.g., 1-5 scale)
        run1: EvaluationRun = EvaluationRunFactory(team=team, status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        for rating in [4, 5, 4, 3, 5]:
            EvaluationResultFactory(
                run=run1,
                evaluator=evaluator,
                team=team,
                output={"result": {"rating": rating}},
            )
        compute_aggregates_for_run(run1)

        # Second run: field "rating" is now categorical (e.g., "good"/"bad")
        run2: EvaluationRun = EvaluationRunFactory(team=team, status=EvaluationRunStatus.COMPLETED)  # ty: ignore[invalid-assignment]
        for rating in ["good", "good", "bad", "good"]:
            EvaluationResultFactory(
                run=run2,
                evaluator=evaluator,
                team=team,
                output={"result": {"rating": rating}},
            )
        compute_aggregates_for_run(run2)

        trend_data = build_trend_data([run1, run2])

        # Should have separate entries for each type
        assert "rating (numeric)" in trend_data["Quality Check"]
        assert "rating (categorical)" in trend_data["Quality Check"]

        # Numeric entry should only have numeric data
        numeric_data = trend_data["Quality Check"]["rating (numeric)"]
        assert numeric_data["type"] == "numeric"
        assert len(numeric_data["points"]) == 1
        assert numeric_data["points"][0]["value"] == 4.2

        # Categorical entry should only have categorical data
        categorical_data = trend_data["Quality Check"]["rating (categorical)"]
        assert categorical_data["type"] == "categorical"
        assert len(categorical_data["points"]) == 1
        assert categorical_data["points"][0]["value"] == "good"
