"""Shared helpers for building and writing evaluation result export data.

Both the per-run results table/CSV (``EvaluationRun.get_table_data`` /
``download_evaluation_run_csv``) and the async bulk export
(``export_evaluation_bulk_results_task``) build the same per-message row shape and
share the same CSV column ordering, so the logic lives here to avoid two diverging
code paths.
"""

import csv
import io
import json
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING

from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import F, OuterRef, QuerySet

from apps.evaluations.const import EVALUATION_RUN_FIXED_HEADERS

if TYPE_CHECKING:
    from apps.evaluations.models import EvaluationResult, Evaluator

_RESULT_CHUNK_SIZE = 500  # results fetched per round trip when streaming an export
_SPOOL_MAX_BYTES = 10 * 1024 * 1024  # a spooled export stays in memory below this size

# Acronyms .title() would otherwise mangle ("Suspected Ai" instead of "Suspected AI")
# when deriving a column header from a field name.
_LABEL_ACRONYMS = {"ai", "id", "url", "llm", "api"}


def _field_label(field_name: str) -> str:
    words = field_name.replace("_", " ").split()
    return " ".join(word.upper() if word.lower() in _LABEL_ACRONYMS else word.capitalize() for word in words)


def annotate_export_fields(queryset: "QuerySet[EvaluationResult]") -> "QuerySet[EvaluationResult]":
    """Annotate the related values the row builders read off each result.

    Loading the message, session and experiment object per row instead is what exhausted the
    worker's memory (issue #4529). Required by both row builders below.
    """
    from apps.evaluations.models import AppliedTag  # noqa: PLC0415 - circular: evaluations.models imports this module

    return queryset.annotate(
        evaluator_name=F("evaluator__name"),
        session_external_id=F("session__external_id"),
        source_session_external_id=F("message__session__external_id"),
        source_experiment_public_id=F("message__session__experiment__public_id"),
        applied_tag_names=ArraySubquery(
            AppliedTag.objects.filter(evaluation_result=OuterRef("pk")).values("tag__name")
        ),
    )


def _populate_message_row_fixed_fields(row_data: OrderedDict, result, include_ids: bool = False) -> None:
    """Populate the fixed/shared fields for a new message row."""
    row_data["session"] = result.session_external_id or ""
    row_data["source_session"] = result.source_session_external_id or ""
    row_data["source_experiment_id"] = (
        str(result.source_experiment_public_id) if result.source_experiment_public_id else ""
    )
    row_data["message_id"] = result.message_id
    row_data["Dataset Input"] = result.input_message
    row_data["Dataset Output"] = result.output_message
    row_data["Generated Response"] = result.output.get("generated_response", "")
    if include_ids:
        row_data["id"] = result.message_id


def _accumulate_result(row_data: OrderedDict, tags: set, result, include_ids: bool = False) -> None:
    """Fold one result into the row being built for its message."""
    if not row_data:
        _populate_message_row_fixed_fields(row_data, result, include_ids=include_ids)

    for key, value in result.output.get("result", {}).items():
        row_data[f"{key} ({result.evaluator_name})"] = value

    # Context is the same for every result on a message; updating each time is idempotent.
    for key, value in result.message_context.items():
        if key != "current_datetime":
            row_data[key] = value

    if result.output.get("error"):
        row_data[f"error ({result.evaluator_name})"] = result.output["error"]

    tags.update(result.applied_tag_names)


def build_evaluation_table_data(results, include_ids: bool = False) -> list[dict]:
    """Aggregate *results* into one row dict per message, holding them all in memory.

    Errors are namespaced per evaluator (``error (EvaluatorName)``) so two evaluators failing
    on one message do not overwrite each other. *include_ids* adds a hidden ``id`` field the
    results table uses for row highlighting. Order-insensitive; prefer
    ``iter_evaluation_table_rows`` when the results can be ordered by message id.
    """
    rows_by_message: dict[int, tuple[OrderedDict, set]] = {}

    for result in results:
        row_data, tags = rows_by_message.setdefault(result.message_id, (OrderedDict(), set()))
        _accumulate_result(row_data, tags, result, include_ids=include_ids)

    return [
        {"#": index, **row_data, "Applied Tags": ", ".join(sorted(tags))}
        for index, (row_data, tags) in enumerate(rows_by_message.values())
    ]


def iter_evaluation_table_rows(queryset: "QuerySet[EvaluationResult]", include_ids: bool = False) -> Iterator[dict]:
    """Yield the same rows as ``build_evaluation_table_data``, one message at a time.

    *queryset* must be ordered by message id: each row is emitted as its group ends.
    """
    results = queryset.iterator(chunk_size=_RESULT_CHUNK_SIZE)
    for index, (_, group) in enumerate(groupby(results, key=attrgetter("message_id"))):
        row_data: OrderedDict = OrderedDict()
        tags: set[str] = set()
        for result in group:
            _accumulate_result(row_data, tags, result, include_ids=include_ids)
        yield {"#": index, **row_data, "Applied Tags": ", ".join(sorted(tags))}


@dataclass(frozen=True)
class CategoricalValue:
    """One possible value of a categorical/binary evaluator output field, as a
    (raw, label) pair. `raw` is what a row's dynamic-column value stringifies to —
    a choice field stores the choice string itself, a binary field stores 0/1.

    `polarity` ("positive"/"negative"/"neutral") drives the results table's badge
    color. There is no schema concept of which choice is "good", so it defaults to the
    first-listed value being positive and the second negative - a real signal for a
    field like ["Acceptable", "Unacceptable"] or a binary field's true/false, but not
    something that generalizes past two values (three or more choices has no obvious
    polarity, so those stay neutral).
    """

    raw: str
    label: str
    polarity: str = "neutral"


@dataclass(frozen=True)
class CategoricalColumn:
    """One evaluator output field whose values are enumerable (choice/binary), keyed by
    the same composite string `build_evaluation_table_data` uses for that column
    (`"{field_name} ({evaluator.name})"`), so it matches straight against a row dict's
    keys for both filtering and badge rendering."""

    column_key: str
    field_label: str
    values: list[CategoricalValue]


def categorical_columns_for_evaluators(evaluators: "list[Evaluator]") -> list[CategoricalColumn]:
    """Every choice/binary output field across *evaluators*, in evaluator then field order.

    Evaluators with no `output_schema` (Python evaluators) contribute nothing.
    """
    result = []
    for evaluator in evaluators:
        schema = (evaluator.params or {}).get("output_schema") or {}
        for field_name, field_def in schema.items():
            field_type = (field_def or {}).get("type")
            if field_type == "choice":
                choices = field_def.get("choices") or []
                if len(choices) == 2:
                    values = [
                        CategoricalValue(raw=choices[0], label=choices[0], polarity="positive"),
                        CategoricalValue(raw=choices[1], label=choices[1], polarity="negative"),
                    ]
                else:
                    values = [CategoricalValue(raw=choice, label=choice) for choice in choices]
            elif field_type == "binary":
                values = [
                    CategoricalValue(raw="1", label=field_def.get("true_label", "True"), polarity="positive"),
                    CategoricalValue(raw="0", label=field_def.get("false_label", "False"), polarity="negative"),
                ]
            else:
                continue
            if not values:
                continue
            result.append(
                CategoricalColumn(
                    column_key=f"{field_name} ({evaluator.name})",
                    field_label=_field_label(field_name),
                    values=values,
                )
            )
    return result


def evaluator_output_columns(evaluators: "list[Evaluator]") -> list[tuple[str, str]]:
    """(column_key, label) for every evaluator output field, any type, in evaluator/field
    order - the results table's curated column set is "one column per output field",
    not "one column per (evaluator, field) pair", so the evaluator name that
    disambiguates `column_key` is dropped from the display label.
    """
    columns = []
    for evaluator in evaluators:
        schema = (evaluator.params or {}).get("output_schema") or {}
        for field_name in schema:
            columns.append((f"{field_name} ({evaluator.name})", _field_label(field_name)))
    return columns


def order_evaluation_headers(all_headers: Iterable[str]) -> list[str]:
    """Fixed headers first, then dynamic columns alphabetically, then error columns last."""
    all_headers = set(all_headers)
    error_headers = sorted(h for h in all_headers if h == "error" or h.startswith("error ("))
    other_headers = sorted(h for h in all_headers if h not in EVALUATION_RUN_FIXED_HEADERS and h not in error_headers)
    return [h for h in EVALUATION_RUN_FIXED_HEADERS if h in all_headers] + other_headers + error_headers


def write_evaluation_csv(writer, rows: Iterable[dict]) -> None:
    """Write *rows* to *writer* using the standard evaluation column ordering.

    The header is the union of every row's keys, so it is only known after the last row. Rows
    are spooled as JSON in the meantime and re-read for the CSV pass.
    """
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+", encoding="utf-8") as spool:
        headers: set[str] = set()
        for row in rows:
            headers.update(row)
            spool.write(json.dumps(row, default=str))
            spool.write("\n")

        if not headers:
            writer.writerow(["No results available yet"])
            return

        ordered_headers = order_evaluation_headers(headers)
        writer.writerow(ordered_headers)
        spool.seek(0)
        for line in spool:
            row = json.loads(line)
            writer.writerow([row.get(header, "") for header in ordered_headers])


def export_evaluation_csv_to_tempfile(rows: Iterable[dict]) -> "tempfile.SpooledTemporaryFile[bytes]":
    """Write the evaluation CSV for *rows* to a binary temp file and return it, seeked to 0.

    Use as a context manager. Binary so it can go straight to storage, which streams it in
    chunks. Mirrors ``apps.experiments.export.export_to_tempfile``.
    """
    # write_evaluation_csv spools the rows to learn the header; the finished CSV lands here.
    tmp = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="wb+")  # noqa: SIM115
    text_wrapper = io.TextIOWrapper(tmp, encoding="utf-8", newline="")
    write_evaluation_csv(csv.writer(text_wrapper), rows)
    text_wrapper.flush()
    # Release the wrapper without closing tmp; the caller still has to read it.
    text_wrapper.detach()
    tmp.seek(0)
    return tmp
