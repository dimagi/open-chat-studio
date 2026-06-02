"""Shared helpers for building and writing evaluation result export data.

Both the per-run results table/CSV (``EvaluationRun.get_table_data`` /
``download_evaluation_run_csv``) and the async bulk export
(``export_evaluation_bulk_results_task``) build the same per-message row shape and
share the same CSV column ordering, so the logic lives here to avoid two diverging
code paths.
"""

from collections import OrderedDict, defaultdict

from apps.evaluations.const import EVALUATION_RUN_FIXED_HEADERS


def _populate_message_row_fixed_fields(row_data: OrderedDict, result, include_ids: bool = False) -> None:
    """Populate the fixed/shared fields for a new message row."""
    source_session = result.message.session if result.message.session_id else None
    row_data["session"] = result.session.external_id if result.session_id else ""
    row_data["source_session"] = source_session.external_id if source_session else ""
    row_data["source_experiment_id"] = (
        str(source_session.experiment.public_id) if source_session and source_session.experiment_id else ""
    )
    row_data["message_id"] = result.message_id
    row_data["Dataset Input"] = result.input_message
    row_data["Dataset Output"] = result.output_message
    row_data["Generated Response"] = result.output.get("generated_response", "")
    if include_ids:
        row_data["id"] = result.message_id


def build_evaluation_table_data(results, include_ids: bool = False) -> list[dict]:
    """Aggregate *results* (an iterable of ``EvaluationResult``) into a list of per-message
    row dicts, combining evaluator columns, message context, and applied tags.

    Errors are namespaced per evaluator (``error (EvaluatorName)``) so that failures from
    different evaluators on the same message are preserved rather than overwritten.

    When *include_ids* is True each row carries a hidden ``id`` field (the message id) used by
    the results table for row highlighting.
    """
    table_by_message: dict[int, OrderedDict] = {}
    tags_by_message: dict[int, set] = defaultdict(set)

    for result in results:
        row_data = table_by_message.get(result.message_id)
        if row_data is None:
            row_data = OrderedDict()
            _populate_message_row_fixed_fields(row_data, result, include_ids=include_ids)
            table_by_message[result.message_id] = row_data

        for key, value in result.output.get("result", {}).items():
            row_data[f"{key} ({result.evaluator.name})"] = value

        # Context is the same for every result on a message; updating each time is idempotent.
        for key, value in result.message_context.items():
            if key != "current_datetime":
                row_data[key] = value

        if result.output.get("error"):
            row_data[f"error ({result.evaluator.name})"] = result.output["error"]

        for applied_tag in result.applied_tags.all():
            tags_by_message[result.message_id].add(applied_tag.tag.name)

    for message_id, row_data in table_by_message.items():
        tags = tags_by_message.get(message_id)
        row_data["Applied Tags"] = ", ".join(sorted(tags)) if tags else ""

    return [{"#": index, **row} for index, row in enumerate(table_by_message.values())]


def write_evaluation_csv(writer, table_data: list[dict]) -> None:
    """Write *table_data* rows to *writer* using the standard evaluation column ordering.

    Fixed headers come first, then alphabetically-sorted dynamic columns, then any
    error columns last.  This is shared between the per-run and bulk-download exports.
    """
    if not table_data:
        writer.writerow(["No results available yet"])
        return

    all_headers: set[str] = set()
    for row in table_data:
        all_headers.update(row.keys())

    error_headers = sorted(h for h in all_headers if h == "error" or h.startswith("error ("))
    other_headers = sorted(h for h in all_headers if h not in EVALUATION_RUN_FIXED_HEADERS and h not in error_headers)
    headers = [h for h in EVALUATION_RUN_FIXED_HEADERS if h in all_headers] + other_headers + error_headers
    writer.writerow(headers)
    for row in table_data:
        writer.writerow([row.get(header, "") for header in headers])
