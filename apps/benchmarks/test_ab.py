import json
from unittest.mock import patch

import pytest

from apps.benchmarks import ab


def test_compare_reports_ratio_and_pair_spread():
    a_runs = [{"t::x": 0.010, "t::only_a": 1.0}, {"t::x": 0.012}]
    b_runs = [{"t::x": 0.011}, {"t::x": 0.012}]

    [row] = ab.compare(a_runs, b_runs)

    assert row["name"] == "t::x"
    assert row["a_median"] == pytest.approx(0.011)
    assert row["b_median"] == pytest.approx(0.0115)
    assert row["ratio"] == pytest.approx(0.0115 / 0.011)
    assert row["pair_ratio_spread"] == pytest.approx(0.1)


def test_main_warms_up_each_side_then_alternates_order(tmp_path, capsys):
    calls = []

    def fake_run_side(checkout, side, result, create_db, extra):
        calls.append((side, create_db))
        median = 0.010 if side == "a" else 0.020
        result.write_text(
            json.dumps(
                {
                    "commit_info": {"id": f"{side}" * 12},
                    "benchmarks": [{"fullname": "t::x", "stats": {"median": median}}],
                }
            )
        )

    with patch.object(ab, "_run_side", fake_run_side):
        ab.main(["--a", "base", "--b", ".", "--pairs", "3", "--out", str(tmp_path)])

    warmup = [("a", True), ("b", True)]
    assert calls == [*warmup, ("a", False), ("b", False), ("b", False), ("a", False), ("a", False), ("b", False)]
    [row] = json.loads((tmp_path / "summary.json").read_text())
    assert row["ratio"] == pytest.approx(2.0)
    assert "| `x` | 10.000 | 20.000 | 2.000 | 0.0% |" in capsys.readouterr().out


def test_compare_spread_uses_only_pairs_with_both_results():
    a_runs = [{"t::x": 0.010}, {"t::x": 0.010}, {}]
    b_runs = [{}, {"t::x": 0.011}, {"t::x": 0.020}]

    [row] = ab.compare(a_runs, b_runs)

    assert row["pair_ratio_spread"] == pytest.approx(0.0)


def test_compare_spread_is_none_without_a_complete_pair():
    [row] = ab.compare([{"t::x": 0.010}, {}], [{}, {"t::x": 0.011}])

    assert row["pair_ratio_spread"] is None


def test_pairs_must_be_positive():
    with pytest.raises(SystemExit):
        ab.main(["--a", "base", "--b", ".", "--pairs", "0"])


def _fake_run_side_with_b_ratio(ratio):
    def fake_run_side(checkout, side, result, create_db, extra):
        median = 0.010 if side == "a" else 0.010 * ratio
        result.write_text(
            json.dumps(
                {
                    "commit_info": {"id": side * 12},
                    "benchmarks": [{"fullname": "t::x", "stats": {"median": median}}],
                }
            )
        )

    return fake_run_side


@pytest.mark.parametrize(("ratio", "exit_status"), [(1.20, 0), (1.30, 1)])
def test_fail_above_fails_only_when_a_ratio_is_over_the_limit(tmp_path, capsys, ratio, exit_status):
    with patch.object(ab, "_run_side", _fake_run_side_with_b_ratio(ratio)):
        status = ab.main(["--a", "base", "--b", ".", "--pairs", "1", "--out", str(tmp_path), "--fail-above", "1.25"])

    assert status == exit_status
    assert ("::error::x is 30% slower than A" in capsys.readouterr().err) == (exit_status == 1)


def test_without_fail_above_a_slow_b_still_exits_zero(tmp_path):
    with patch.object(ab, "_run_side", _fake_run_side_with_b_ratio(3.0)):
        assert ab.main(["--a", "base", "--b", ".", "--pairs", "1", "--out", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    ("only_on", "message"),
    [
        pytest.param("a", "::error::y ran only on A", id="missing-from-b"),
        pytest.param("b", "::error::y ran only on B", id="missing-from-a"),
    ],
)
def test_fail_above_fails_when_a_benchmark_ran_on_only_one_side(tmp_path, capsys, only_on, message):
    def fake_run_side(checkout, side, result, create_db, extra):
        benchmarks = [{"fullname": "t::x", "stats": {"median": 0.010}}]
        if side == only_on:
            benchmarks.append({"fullname": "t::y", "stats": {"median": 0.010}})
        result.write_text(json.dumps({"commit_info": {"id": side * 12}, "benchmarks": benchmarks}))

    with patch.object(ab, "_run_side", fake_run_side):
        status = ab.main(["--a", "base", "--b", ".", "--pairs", "1", "--out", str(tmp_path), "--fail-above", "1.25"])

    assert status == 1
    assert message in capsys.readouterr().err
