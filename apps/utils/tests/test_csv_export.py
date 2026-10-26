import pytest

from apps.utils.csv_export import restore_csv_formula


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("'=x", "=x", id="guarded-formula-restored"),
        pytest.param("'-1", "-1", id="guarded-negative-number-restored"),
        pytest.param("'hello", "'hello", id="apostrophe-before-text-kept"),
        pytest.param("=x", "=x", id="unguarded-value-unchanged"),
        pytest.param("''=x", "''=x", id="apostrophe-before-apostrophe-unchanged"),
        pytest.param(5, 5, id="non-string-unchanged"),
    ],
)
def test_restore_csv_formula(value, expected):
    assert restore_csv_formula(value) == expected
