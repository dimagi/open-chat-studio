"""Helpers for CSV exports containing user-controlled values."""

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_formula(value):
    """Prefix user-controlled values that could execute as a spreadsheet formula.

    The csv module's quoting protects against delimiter injection but not formula
    injection: a value starting with =, +, -, or @ runs as a formula when the export
    is opened in Excel/Sheets. Prepending an apostrophe forces it to be read as text.
    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value
