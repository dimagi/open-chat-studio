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


def test_pairs_must_be_positive():
    with pytest.raises(SystemExit):
        ab.main(["--a", "base", "--b", ".", "--pairs", "0"])
