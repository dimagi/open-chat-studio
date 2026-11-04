import pytest

from apps.utils.csv_export import neutralize_csv_formula, restore_csv_formula


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("=x", "'=x", id="formula-guarded"),
        pytest.param("-5", "'-5", id="negative-number-guarded"),
        pytest.param("'=x", "''=x", id="apostrophe-before-formula-guarded"),
        pytest.param("''=x", "'''=x", id="apostrophes-before-formula-guarded"),
        pytest.param("'hello", "'hello", id="apostrophe-before-text-unchanged"),
        pytest.param("'", "'", id="lone-apostrophe-unchanged"),
        pytest.param("hello", "hello", id="plain-text-unchanged"),
        pytest.param(-5, -5, id="non-string-unchanged"),
    ],
)
def test_neutralize_csv_formula(value, expected):
    assert neutralize_csv_formula(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("'=x", "=x", id="guarded-formula-restored"),
        pytest.param("'-1", "-1", id="guarded-negative-number-restored"),
        pytest.param("''=x", "'=x", id="one-apostrophe-before-apostrophe-formula-restored"),
        pytest.param("'hello", "'hello", id="apostrophe-before-text-kept"),
        pytest.param("''", "''", id="apostrophes-only-kept"),
        pytest.param("=x", "=x", id="unguarded-value-unchanged"),
        pytest.param(5, 5, id="non-string-unchanged"),
    ],
)
def test_restore_csv_formula(value, expected):
    assert restore_csv_formula(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("=x", id="equals"),
        pytest.param("-5", id="minus"),
        pytest.param("+1 (555) 123", id="plus"),
        pytest.param("@x", id="at"),
        pytest.param("\t=1", id="tab"),
        pytest.param("\rx", id="carriage-return"),
        pytest.param("'=x", id="apostrophe-equals"),
        pytest.param("'-5", id="apostrophe-minus"),
        pytest.param("''=x", id="two-apostrophes-equals"),
        pytest.param("'''=x", id="three-apostrophes-equals"),
        pytest.param("'", id="lone-apostrophe"),
        pytest.param("''", id="two-apostrophes"),
        pytest.param("'hello", id="apostrophe-text"),
        pytest.param("", id="empty"),
        pytest.param("hello", id="plain-text"),
        pytest.param("'\t[1, 2]", id="apostrophe-tab-list"),
    ],
)
def test_restore_undoes_neutralize(value):
    assert restore_csv_formula(neutralize_csv_formula(value)) == value
