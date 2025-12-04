import pytest

from apps.evaluations.aggregation import compute_aggregates_for_run
from apps.evaluations.aggregators import aggregate_field, get_aggregators_for_value
from apps.evaluations.models import EvaluationRunStatus
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
        run = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)
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
        run = EvaluationRunFactory(status=EvaluationRunStatus.COMPLETED)
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
        assert trend_data["Quality Check"]["score"]["type"] == "numeric"
        assert trend_data["Quality Check"]["score"]["points"][0]["value"] == 0.85
        assert trend_data["Quality Check"]["rating"]["type"] == "categorical"
        assert trend_data["Quality Check"]["rating"]["categories"] == ["bad", "good"]

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

        assert "score" in trend_data["Test Evaluator"]
        assert "internal" not in trend_data["Test Evaluator"]
