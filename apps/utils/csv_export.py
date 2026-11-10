"""Helpers for CSV exports containing user-controlled values."""

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_formula(value):
    """Prefix user-controlled values that could execute as a spreadsheet formula.

    The csv module's quoting protects against delimiter injection but not formula
    injection: a value starting with =, +, -, or @ runs as a formula when the export
    is opened in Excel/Sheets. Prepending an apostrophe forces it to be read as text.
    A value whose leading apostrophes precede a formula character also gets one, so
    `restore_csv_formula` can tell it apart from a guarded value.
    """
    if isinstance(value, str) and _starts_with_formula(value):
        return f"'{value}"
    return value


def restore_csv_formula(value):
    """Undo `neutralize_csv_formula` for a value read back from an exported CSV."""
    if isinstance(value, str) and _is_guarded(value):
        return value[1:]
    return value


def _is_guarded(value: str) -> bool:
    return value.startswith("'") and _starts_with_formula(value[1:])


def _starts_with_formula(value: str) -> bool:
    return value.lstrip("'").startswith(CSV_FORMULA_PREFIXES)
