import csv
import hashlib
import io
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tiktoken
from bs4 import UnicodeDammit
from django.conf import settings

from apps.documents.models import FAILURE_REASON_MAX_LENGTH

ROW_IMPORT_EXTENSIONS: dict[str, str | None] = {".csv": None, ".tsv": "\t"}
SNIFF_SAMPLE_CHARS = 4096
SNIFF_DELIMITERS = ",;\t|"


class RowImportError(Exception):
    pass


@dataclass(frozen=True)
class ParsedRow:
    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class ParsedSheet:
    headers: list[str]
    rows: list[ParsedRow]


@dataclass(frozen=True)
class RowFailure:
    row_number: int
    reason: str


def parse_sheet(data: bytes, *, filename: str, max_rows: int | None = None) -> ParsedSheet:
    """Parse a CSV or TSV into header names and one `ParsedRow` per non-blank data row."""
    max_rows = settings.COLLECTION_ROW_IMPORT_MAX_ROWS if max_rows is None else max_rows
    delimiter = _delimiter_for(filename)
    text = _decode(data)
    if delimiter is None:
        delimiter = _sniff_delimiter(text)

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = _read_headers(reader)
        rows: list[ParsedRow] = []
        kept_rows = 0
        for row_number, values in enumerate(reader, start=1):
            if not any(value.strip() for value in values):
                continue
            if len(values) != len(headers):
                raise RowImportError(f"Row {row_number} has {len(values)} values but the header has {len(headers)}")
            kept_rows += 1
            if kept_rows > max_rows:
                raise RowImportError(f"The file has more than {max_rows} rows")
            rows.append(ParsedRow(row_number=row_number, values=dict(zip(headers, values, strict=True))))
    except csv.Error as exc:
        raise RowImportError(f"Could not parse the file: {exc}") from None
    if not rows:
        raise RowImportError("The file has no data rows")
    return ParsedSheet(headers=headers, rows=rows)


def _delimiter_for(filename: str) -> str | None:
    extension = Path(filename).suffix.lower()
    if extension not in ROW_IMPORT_EXTENSIONS:
        raise RowImportError("Row import accepts csv or tsv files only")
    return ROW_IMPORT_EXTENSIONS[extension]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        detected = UnicodeDammit(data).unicode_markup
        if detected is None:
            raise RowImportError("Unable to detect file encoding") from None
        return detected.lstrip("﻿")


def _sniff_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:SNIFF_SAMPLE_CHARS], delimiters=SNIFF_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _read_headers(reader) -> list[str]:
    headers = next(reader, None)
    if not headers or not any(header.strip() for header in headers):
        raise RowImportError("The file has no header row")
    headers = [header.strip() for header in headers]
    seen: set[str] = set()
    for position, header in enumerate(headers, start=1):
        if not header:
            raise RowImportError(f"Column {position} has no name")
        if header in seen:
            raise RowImportError(f"Duplicate column name: {header}")
        seen.add(header)
    return headers


def render_row(filename: str, headers: list[str], row: ParsedRow) -> str:
    """Render a row as the sheet name followed by one `column: value` line per column."""
    lines = [filename, *(f"{header}: {row.values[header]}" for header in headers)]
    return "\n".join(lines).replace("\x00", "")


def row_metadata(row: ParsedRow, metadata_columns: list[str]) -> dict[str, str]:
    return {column: row.values[column] for column in metadata_columns}


def content_hash(text: str, metadata: dict[str, str]) -> str:
    payload = json.dumps({"text": text, "metadata": metadata}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@cache
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def oversized_row_numbers(sheet: ParsedSheet, *, filename: str, max_tokens: int | None = None) -> list[int]:
    max_tokens = settings.COLLECTION_ROW_IMPORT_MAX_ROW_TOKENS if max_tokens is None else max_tokens
    return [row.row_number for row in sheet.rows if count_tokens(render_row(filename, sheet.headers, row)) > max_tokens]


def format_row_failures(failures: list[RowFailure], total_rows: int) -> str:
    if not failures:
        return ""
    details = "; ".join(f"row {failure.row_number}: {failure.reason}" for failure in failures)
    details = details[0].upper() + details[1:]
    summary = f"{len(failures)} of {total_rows} rows failed to index. {details}"
    return summary[:FAILURE_REASON_MAX_LENGTH]
