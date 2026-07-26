from unittest.mock import patch

import pytest

from apps.teams.models import Team
from apps.teams.tasks import start_team_files_export
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def team():
    return Team.objects.create(name="Acme", slug="acme")


@pytest.mark.django_db()
class TestFilesExportTracking:
    def test_mark_started_and_finished(self, team):
        assert not team.files_export_in_progress

        team.mark_files_export_started("task-1")
        assert team.files_export_in_progress
        assert team.files_export_task_id == "task-1"

        team.mark_files_export_finished()
        team.refresh_from_db()
        assert not team.files_export_in_progress

    def test_mark_finished_records_the_completed_export_file(self, team):
        export = FileFactory(team=team, file__data=b"z", file__filename="e.zip")
        team.mark_files_export_started("task-1")
        team.mark_files_export_finished(export_file_id=export.id)
        team.refresh_from_db()
        assert not team.files_export_in_progress
        assert team.files_export_id == export.id


@pytest.mark.django_db()
class TestStartTeamFilesExport:
    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async")
    def test_records_task_before_dispatch(self, apply_async, team):
        task_id = start_team_files_export(team)

        assert task_id is not None
        team.refresh_from_db()
        assert team.files_export_task_id == task_id
        apply_async.assert_called_once_with(args=[team.id], task_id=task_id)

    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async")
    def test_reuses_in_flight_export(self, apply_async, team):
        team.mark_files_export_started("other-task")

        assert start_team_files_export(team) == "other-task"

        apply_async.assert_not_called()
        team.refresh_from_db()
        assert team.files_export_task_id == "other-task"

    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async", side_effect=RuntimeError("boom"))
    def test_clears_task_when_dispatch_fails(self, apply_async, team):
        with pytest.raises(RuntimeError, match="boom"):
            start_team_files_export(team)

        team.refresh_from_db()
        assert not team.files_export_in_progress
