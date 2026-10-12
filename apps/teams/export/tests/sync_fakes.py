"""Stand-ins for the source server shared by the sync command tests."""

from apps.teams.export.manifest import schema_checksum

PAST = "2020-01-02T03:04:05+00:00"


class FakeClient:
    def __init__(self, manifest, rows_by_resource):
        self.manifest = manifest
        self.rows_by_resource = rows_by_resource
        self.iter_calls = []
        self.selections = []
        self.get_team_calls = 0

    def get_manifest(self):
        return self.manifest

    def get_team(self):
        # The team is fetched on its own from the dedicated ``team/`` endpoint, not the manifest loop.
        self.get_team_calls += 1
        return self.rows_by_resource["teams"][0]

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        self.iter_calls.append((resource, start_cursor))
        if selection not in self.selections:
            self.selections.append(selection)
        rows = list(self.rows_by_resource.get(resource, []))
        return iter([rows]) if rows else iter([])

    def get_file_content(self, file_id):
        return b""


def make_manifest(entries, checksum=None):
    return {"schema_checksum": checksum if checksum is not None else schema_checksum(), "entries": entries}


def team_row():
    """The source team, ready to export: in migration mode with a public key registered."""
    return {
        "id": 9001,
        "name": "Imported",
        "slug": "imported-team-z",
        "feature_flags": [],
        "is_migrating": True,
        "has_public_key": True,
        "created_at": PAST,
        "updated_at": PAST,
    }


def select_chatbots(rows, *public_ids):
    """Mark the given chatbots as the ones the source team exports."""
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": pid, "name": pid.upper()} for pid in public_ids]
    return rows
