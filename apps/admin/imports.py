import csv
import io
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.teams.metadata import get_team_metadata_fields
from apps.teams.models import Team

SLUG_COLUMN = "Slug"


@dataclass
class TeamMetadataImportResult:
    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def import_team_metadata_from_csv(uploaded_file) -> TeamMetadataImportResult:
    """Bulk-update team metadata from a CSV in the team-metadata export format.

    Teams are matched by the ``Slug`` column; metadata columns are matched to configured
    fields by their label. Only columns present in the CSV are written, so a partial CSV
    leaves other metadata keys untouched. Unknown slugs are reported and skipped.
    """
    result = TeamMetadataImportResult()
    reader = csv.DictReader(io.TextIOWrapper(uploaded_file, encoding="utf-8-sig"))

    present_fields, column_error = _resolve_columns(reader.fieldnames or [])
    if column_error:
        result.errors.append(column_error)
        return result

    rows = list(reader)
    teams_by_slug = _teams_by_slug(rows)
    for line_number, row in enumerate(rows, start=2):  # row 1 is the header
        slug, error = _apply_row(line_number, row, present_fields, teams_by_slug)
        if error:
            result.errors.append(error)
        else:
            result.updated.append(slug)
    return result


def _resolve_columns(fieldnames) -> tuple[dict[str, dict], str | None]:
    """Map present CSV headers to their field definitions, or return an error describing why we can't."""
    if SLUG_COLUMN not in fieldnames:
        return {}, f"CSV must include a '{SLUG_COLUMN}' column."

    fields_by_label = {f["label"]: f for f in get_team_metadata_fields()}
    present_fields = {header: fields_by_label[header] for header in fieldnames if header in fields_by_label}
    if not present_fields:
        return {}, "CSV has no columns matching configured metadata fields."
    return present_fields, None


def _teams_by_slug(rows) -> dict[str, Team]:
    slugs = [(row.get(SLUG_COLUMN) or "").strip() for row in rows]
    return {team.slug: team for team in Team.objects.filter(slug__in=[s for s in slugs if s])}


def _apply_row(line_number, row, present_fields, teams_by_slug) -> tuple[str | None, str | None]:
    """Update one team from a CSV row, returning ``(updated_slug, error)`` (one is always None)."""
    slug = (row.get(SLUG_COLUMN) or "").strip()
    if not slug:
        return None, f"Row {line_number}: missing slug."
    team = teams_by_slug.get(slug)
    if team is None:
        return None, f"Row {line_number}: no team with slug '{slug}'."

    values = {}
    for header, field_def in present_fields.items():
        value = (row.get(header) or "").strip()
        error = _validate_value(line_number, field_def, value)
        if error:
            return None, error
        values[field_def["key"]] = value

    metadata = dict(team.metadata or {})
    metadata.update(values)
    team.metadata = metadata
    team.save(update_fields=["metadata"])
    return slug, None


def _validate_value(line_number, field_def, value) -> str | None:
    """Return an error string for an invalid cell value, or None if it's acceptable.

    Blank values are always allowed (they clear the field); non-blank values must satisfy
    the field's ``type`` constraints.
    """
    if not value:
        return None
    field_type = field_def["type"]
    label = field_def["label"]
    if field_type == "select" and value not in field_def["options"]:
        allowed = ", ".join(field_def["options"])
        return f"Row {line_number}: '{value}' is not a valid option for '{label}' (expected one of: {allowed})."
    if field_type == "email":
        try:
            validate_email(value)
        except ValidationError:
            return f"Row {line_number}: '{value}' is not a valid email for '{label}'."
    return None
