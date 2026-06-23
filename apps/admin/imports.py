import csv
import io
from dataclasses import dataclass, field

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
        _apply_row(line_number, row, present_fields, teams_by_slug, result)
    return result


def _resolve_columns(fieldnames) -> tuple[dict[str, str], str | None]:
    """Map present CSV headers to field keys, or return an error describing why we can't."""
    if SLUG_COLUMN not in fieldnames:
        return {}, f"CSV must include a '{SLUG_COLUMN}' column."

    label_to_key = {f["label"]: f["key"] for f in get_team_metadata_fields()}
    present_fields = {header: label_to_key[header] for header in fieldnames if header in label_to_key}
    if not present_fields:
        return {}, "CSV has no columns matching configured metadata fields."
    return present_fields, None


def _teams_by_slug(rows) -> dict[str, Team]:
    slugs = [(row.get(SLUG_COLUMN) or "").strip() for row in rows]
    return {team.slug: team for team in Team.objects.filter(slug__in=[s for s in slugs if s])}


def _apply_row(line_number, row, present_fields, teams_by_slug, result) -> None:
    slug = (row.get(SLUG_COLUMN) or "").strip()
    if not slug:
        result.errors.append(f"Row {line_number}: missing slug.")
        return
    team = teams_by_slug.get(slug)
    if team is None:
        result.errors.append(f"Row {line_number}: no team with slug '{slug}'.")
        return

    metadata = dict(team.metadata or {})
    for header, key in present_fields.items():
        metadata[key] = (row.get(header) or "").strip()
    team.metadata = metadata
    team.save(update_fields=["metadata"])
    result.updated.append(slug)
