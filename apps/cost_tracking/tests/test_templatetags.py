"""Tests for cost_tracking template filters."""

from decimal import Decimal

import pytest

from apps.cost_tracking.templatetags.cost_tracking import cost_display, per_million


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, "0.00", id="none"),
        pytest.param(Decimal("0"), "0.00", id="exact-zero"),
        pytest.param(Decimal("0.0005"), "0.0005", id="sub-cent"),
        pytest.param(Decimal("0.00045720"), "0.0005", id="sub-cent-rounds-up"),
        pytest.param(Decimal("0.009"), "0.0090", id="just-under-one-cent"),
        pytest.param(Decimal("0.01"), "0.01", id="exactly-one-cent"),
        pytest.param(Decimal("1.234"), "1.23", id="normal-rounds-down"),
        pytest.param(Decimal("1.235"), "1.24", id="normal-rounds-up-banker"),
        pytest.param(Decimal("1000"), "1000.00", id="large"),
    ],
)
def test_cost_display(value, expected):
    assert cost_display(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, "0.00", id="none"),
        pytest.param(Decimal("0"), "0.00", id="zero"),
        pytest.param(Decimal("0.003"), "3.00", id="per-1k-to-per-1m"),
        pytest.param(Decimal("0.00001"), "0.01", id="sub-cent-per-million"),
        pytest.param(Decimal("0.015"), "15.00", id="fifteen-per-million"),
        pytest.param(Decimal("0.0000045720"), "0.00", id="rounds-to-zero"),
    ],
)
def test_per_million(value, expected):
    assert per_million(value) == expected
