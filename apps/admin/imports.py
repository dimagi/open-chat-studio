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
    label_to_key = {f["label"]: f["key"] for f in get_team_metadata_fields()}

    reader = csv.DictReader(io.TextIOWrapper(uploaded_file, encoding="utf-8-sig"))
    fieldnames = reader.fieldnames or []
    if SLUG_COLUMN not in fieldnames:
        result.errors.append(f"CSV must include a '{SLUG_COLUMN}' column.")
        return result

    present_fields = {header: label_to_key[header] for header in fieldnames if header in label_to_key}
    if not present_fields:
        result.errors.append("CSV has no columns matching configured metadata fields.")
        return result

    rows = list(reader)
    slugs = [(row.get(SLUG_COLUMN) or "").strip() for row in rows]
    teams_by_slug = {team.slug: team for team in Team.objects.filter(slug__in=[s for s in slugs if s])}

    for line_number, row in enumerate(rows, start=2):  # row 1 is the header
        slug = (row.get(SLUG_COLUMN) or "").strip()
        if not slug:
            result.errors.append(f"Row {line_number}: missing slug.")
            continue
        team = teams_by_slug.get(slug)
        if team is None:
            result.errors.append(f"Row {line_number}: no team with slug '{slug}'.")
            continue

        metadata = dict(team.metadata or {})
        for header, key in present_fields.items():
            metadata[key] = (row.get(header) or "").strip()
        team.metadata = metadata
        team.save(update_fields=["metadata"])
        result.updated.append(slug)

    return result
