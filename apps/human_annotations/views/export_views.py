import csv
import json
import re

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..models import Annotation, AnnotationItem, AnnotationItemStatus, AnnotationQueue, AnnotationStatus


def _safe_filename(name: str) -> str:
    """Sanitize a string for use in Content-Disposition filename."""
    return re.sub(r"[^\w\s\-.]", "_", name).strip()


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_csv_formula(value):
    """Prefix annotator-controlled values that could execute as a spreadsheet formula.

    csv.DictWriter's quoting protects against delimiter injection but not formula
    injection: a value starting with =, +, -, or @ runs as a formula when the export
    is opened in Excel/Sheets. Prepending an apostrophe forces it to be read as text.
    """
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


class ExportAnnotations(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.view_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        export_format = request.GET.get("format", "csv")
        annotations = Annotation.objects.filter(
            item__queue=queue,
            status=AnnotationStatus.SUBMITTED,
        ).select_related(
            "item",
            "item__session",
            "item__message",
            "item__message__chat__experiment_session",
            "reviewer",
        )
        flagged_items = AnnotationItem.objects.filter(queue=queue, status=AnnotationItemStatus.FLAGGED).select_related(
            "session", "message", "message__chat__experiment_session"
        )

        if export_format == "jsonl":
            return self._export_jsonl(queue, annotations, flagged_items)
        return self._export_csv(queue, annotations, flagged_items)

    def _get_session_external_id(self, item):
        if item.session_id:
            return str(item.session.external_id)
        if item.message_id:
            return str(item.message.chat.experiment_session.external_id)
        return ""

    def _group_annotations_by_item(self, annotations):
        by_item = {}
        for ann in annotations:
            by_item.setdefault(ann.item_id, []).append(ann)
        return by_item

    def _item_metadata(self, item_annotations, flagged_item_ids):
        """Per-item facts shared by both export formats: authoritative annotator, timestamp, flag state."""
        item = item_annotations[0].item
        authoritative = next((a for a in item_annotations if a.is_authoritative), None)
        return {
            "item": item,
            "authoritative_email": authoritative.reviewer.email if authoritative else "",
            "annotated_at": max(a.created_at for a in item_annotations).isoformat(),
            "is_flagged": item.pk in flagged_item_ids,
        }

    def _pivot_annotations(self, annotations, flagged_items, schema_fields):
        """Pivot (item, annotation) rows into (item, schema field) rows, one column per annotator.

        `annotations` and `flagged_items` are the same querysets ExportAnnotations.get() builds.
        Returns (fieldnames, rows) where every row dict has every key in fieldnames present.
        """
        flagged_items = list(flagged_items)
        flagged_item_ids = {item.pk for item in flagged_items}
        by_item = self._group_annotations_by_item(annotations)

        annotator_emails = sorted({ann.reviewer.email for ann in annotations})

        fieldnames = [
            "item_id",
            "item_type",
            "session_id",
            "flagged",
            "flags",
            "field",
            "authoritative_annotator",
            "annotated_at",
        ] + annotator_emails

        rows = []
        for item_annotations in by_item.values():
            rows.extend(self._pivot_item_rows(item_annotations, schema_fields, annotator_emails, flagged_item_ids))
        for item in flagged_items:
            if item.pk in by_item:
                continue
            rows.append(self._pivot_flagged_row(item, annotator_emails))

        return fieldnames, rows

    def _pivot_item_rows(self, item_annotations, schema_fields, annotator_emails, flagged_item_ids):
        """Build one CSV row per schema field for a single item's annotations."""
        meta = self._item_metadata(item_annotations, flagged_item_ids)
        item = meta["item"]
        data_by_email = {ann.reviewer.email: ann.data for ann in item_annotations}

        base = {
            "item_id": item.pk,
            "item_type": item.item_type,
            "session_id": self._get_session_external_id(item),
            "flagged": meta["is_flagged"],
            "flags": json.dumps(item.flags),
            "authoritative_annotator": meta["authoritative_email"],
            "annotated_at": meta["annotated_at"],
        }
        rows = []
        for field in schema_fields:
            row = dict(base, field=field)
            for email in annotator_emails:
                row[email] = _neutralize_csv_formula(data_by_email.get(email, {}).get(field, ""))
            rows.append(row)
        return rows

    def _pivot_flagged_row(self, item, annotator_emails):
        """Build the single blank row representing a flagged item with no annotations."""
        row = {
            "item_id": item.pk,
            "item_type": item.item_type,
            "session_id": self._get_session_external_id(item),
            "flagged": True,
            "flags": json.dumps(item.flags),
            "field": "",
            "authoritative_annotator": "",
            "annotated_at": "",
        }
        for email in annotator_emails:
            row[email] = ""
        return row

    def _export_csv(self, queue, annotations, flagged_items):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.csv"'

        schema_fields = list(queue.schema.keys())
        fieldnames, rows = self._pivot_annotations(annotations, flagged_items, schema_fields)

        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

        return response

    def _export_jsonl(self, queue, annotations, flagged_items):
        schema_fields = list(queue.schema.keys())
        flagged_items = list(flagged_items)
        flagged_item_ids = {item.pk for item in flagged_items}
        by_item = self._group_annotations_by_item(annotations)

        lines = [
            json.dumps(self._jsonl_item_record(item_annotations, schema_fields, flagged_item_ids))
            for item_annotations in by_item.values()
        ]
        lines.extend(json.dumps(self._jsonl_flagged_record(item)) for item in flagged_items if item.pk not in by_item)

        content = "\n".join(lines)
        response = HttpResponse(content, content_type="application/jsonl")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.jsonl"'
        return response

    def _jsonl_item_record(self, item_annotations, schema_fields, flagged_item_ids):
        """One JSONL record per item: item-level facts once, per-field values keyed by annotator email."""
        meta = self._item_metadata(item_annotations, flagged_item_ids)
        item = meta["item"]
        annotators = sorted(item_annotations, key=lambda a: a.reviewer.email)
        return {
            "item_id": item.pk,
            "item_type": item.item_type,
            "session_id": self._get_session_external_id(item),
            "flagged": meta["is_flagged"],
            "flags": item.flags,
            "authoritative_annotator": meta["authoritative_email"],
            "annotated_at": meta["annotated_at"],
            "fields": {
                field: {ann.reviewer.email: ann.data.get(field) for ann in annotators} for field in schema_fields
            },
        }

    def _jsonl_flagged_record(self, item):
        """A flagged item with no annotations: item-level facts with empty fields."""
        return {
            "item_id": item.pk,
            "item_type": item.item_type,
            "session_id": self._get_session_external_id(item),
            "flagged": True,
            "flags": item.flags,
            "authoritative_annotator": "",
            "annotated_at": "",
            "fields": {},
        }
