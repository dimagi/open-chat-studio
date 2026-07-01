import io
import zipfile
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.files.models import File, FilePurpose
from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.models import Team
from apps.teams.tasks import create_team_files_zip_task
from apps.users.models import CustomUser
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def team():
    return Team.objects.create(name="Acme", slug="acme")


@pytest.fixture()
def admin(team):
    user = CustomUser.objects.create(username="admin@acme.com")
    make_user_team_owner(team, user)
    return user


@pytest.fixture()
def member(team):
    user = CustomUser.objects.create(username="member@acme.com")
    add_user_to_team(team, user)
    return user


def _run_task(team):
    with patch("apps.teams.tasks.ProgressRecorder"):
        return create_team_files_zip_task(team.id)


def _zip_from_task(team):
    export = File.objects.get(id=_run_task(team))
    with export.file.open("rb") as fh:
        return zipfile.ZipFile(io.BytesIO(fh.read()))


@pytest.mark.django_db()
class TestCreateTeamFilesZipTask:
    def test_includes_team_working_files(self, team):
        f1 = FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        f2 = FileFactory(team=team, file__data=b"world", file__filename="b.txt")
        zf = _zip_from_task(team)
        assert set(zf.namelist()) == {f1.file.name, f2.file.name}
        assert zf.read(f1.file.name) == b"hello"
        assert zf.read(f2.file.name) == b"world"
        assert zf.testzip() is None

    def test_excludes_archived_files(self, team):
        keep = FileFactory(team=team, file__data=b"x", file__filename="keep.txt")
        FileFactory(team=team, is_archived=True, file__data=b"y", file__filename="drop.txt")
        assert _zip_from_task(team).namelist() == [keep.file.name]

    def test_excludes_versioned_copies(self, team):
        working = FileFactory(team=team, file__data=b"w", file__filename="w.txt")
        FileFactory(team=team, working_version=working, file__data=b"v", file__filename="v.txt")
        assert _zip_from_task(team).namelist() == [working.file.name]

    def test_excludes_data_export_artifacts(self, team):
        keep = FileFactory(team=team, file__data=b"x", file__filename="keep.txt")
        FileFactory(
            team=team,
            purpose=FilePurpose.DATA_EXPORT,
            file__data=b"z",
            file__filename="prev-export.zip",
        )
        assert _zip_from_task(team).namelist() == [keep.file.name]

    def test_excludes_other_teams_files(self, team):
        mine = FileFactory(team=team, file__data=b"mine", file__filename="mine.txt")
        other = Team.objects.create(name="Other", slug="other")
        FileFactory(team=other, file__data=b"theirs", file__filename="theirs.txt")
        assert _zip_from_task(team).namelist() == [mine.file.name]

    def test_skips_files_that_cannot_be_read(self, team):
        present = FileFactory(team=team, file__data=b"present", file__filename="present.txt")
        gone = FileFactory(team=team, file__data=b"gone", file__filename="gone.txt")
        gone.file.storage.delete(gone.file.name)  # row points at a file missing from storage
        File.objects.create(team=team, name="external-only")  # row with an empty file field
        assert _zip_from_task(team).namelist() == [present.file.name]

    def test_empty_team_produces_valid_empty_zip(self, team):
        zf = _zip_from_task(team)
        assert zf.namelist() == []
        assert zf.testzip() is None

    def test_creates_data_export_file_with_expiry(self, team):
        FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        export = File.objects.get(id=_run_task(team))
        assert export.team_id == team.id
        assert export.purpose == FilePurpose.DATA_EXPORT
        assert export.content_type == "application/zip"
        assert export.expiry_date is not None
        assert f"team-{team.slug}-files-" in export.name
        assert export.name.endswith(".zip")

    def test_generated_export_is_excluded_from_future_exports(self, team):
        FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        first = _run_task(team)  # creates a DATA_EXPORT artifact
        zf = File.objects.get(id=_run_task(team))
        with zf.file.open("rb") as fh:
            names = zipfile.ZipFile(io.BytesIO(fh.read())).namelist()
        assert File.objects.get(id=first).file.name not in names

    def test_records_export_on_team_and_clears_task_id(self, team):
        team.mark_files_export_started("stale-task-id")
        FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        export_id = _run_task(team)
        team.refresh_from_db()
        assert team.files_export_id == export_id
        assert team.files_export_task_id == ""


@pytest.mark.django_db()
class TestTeamFilesExportField:
    def test_deleting_the_export_file_nulls_out_the_team_reference(self, team):
        export = FileFactory(team=team, purpose=FilePurpose.DATA_EXPORT, file__data=b"z", file__filename="e.zip")
        team.mark_files_export_started("task-1")
        team.mark_files_export_finished(export_file_id=export.id)
        export.delete()
        team.refresh_from_db()
        assert team.files_export_id is None


def _download_files_url(team):
    return reverse("single_team:download_team_files", args=[team.slug])


@pytest.mark.django_db()
class TestDownloadTeamFilesView:
    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async")
    def test_admin_post_starts_task_and_renders_progress(self, apply_async, client, team, admin):
        client.force_login(admin)
        response = client.post(_download_files_url(team))
        assert response.status_code == 200
        apply_async.assert_called_once()
        team.refresh_from_db()
        assert team.files_export_task_id
        content = response.content.decode()
        assert "ocs-download-progress-root" in content
        assert team.files_export_task_id in content

    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async")
    def test_member_forbidden(self, apply_async, client, team, member):
        client.force_login(member)
        response = client.post(_download_files_url(team))
        assert response.status_code == 403
        apply_async.assert_not_called()

    def test_get_not_allowed(self, client, team, admin):
        client.force_login(admin)
        response = client.get(_download_files_url(team))
        assert response.status_code == 405

    @patch("apps.teams.tasks.create_team_files_zip_task.apply_async")
    def test_resumes_existing_export_without_dispatching_again(self, apply_async, client, team, admin):
        team.mark_files_export_started("existing-task")
        client.force_login(admin)
        response = client.post(_download_files_url(team))
        assert response.status_code == 200
        apply_async.assert_not_called()
        assert "existing-task" in response.content.decode()


def _manage_team_url(team):
    return reverse("single_team:manage_team", args=[team.slug])


@pytest.mark.django_db()
class TestDownloadButtonVisibility:
    @pytest.mark.parametrize(
        ("user_fixture", "should_see"),
        [
            pytest.param("admin", True, id="admin-sees-button"),
            pytest.param("member", False, id="member-no-button"),
        ],
    )
    def test_button_visibility(self, request, client, team, user_fixture, should_see):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(_manage_team_url(team))
        assert (_download_files_url(team) in response.content.decode()) is should_see


@pytest.mark.django_db()
class TestManageTeamExportState:
    def test_shows_resumed_progress_when_task_in_flight(self, client, team, admin):
        team.mark_files_export_started("abc")
        client.force_login(admin)
        with patch("apps.teams.views.manage_team_views.AsyncResult") as async_result:
            async_result.return_value.state = "STARTED"
            response = client.get(_manage_team_url(team))
        content = response.content.decode()
        assert "ocs-download-progress-root" in content
        assert "abc" in content
        team.refresh_from_db()
        assert team.files_export_task_id == "abc"

    def test_clears_stale_task_id_and_hides_progress_when_task_finished(self, client, team, admin):
        team.mark_files_export_started("abc")
        client.force_login(admin)
        with patch("apps.teams.views.manage_team_views.AsyncResult") as async_result:
            async_result.return_value.state = "SUCCESS"
            response = client.get(_manage_team_url(team))
        assert "ocs-download-progress-root" not in response.content.decode()
        team.refresh_from_db()
        assert team.files_export_task_id == ""

    def test_shows_ready_download_link_for_previous_export(self, client, team, admin):
        FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        export_id = _run_task(team)
        client.force_login(admin)
        response = client.get(_manage_team_url(team))
        content = response.content.decode()
        assert "ocs-download-progress-root" not in content
        assert "ocs-ready-download-link" in content
        assert f"/files/{export_id}/?allow_s3" in content

    def test_ready_link_disappears_after_export_file_deleted(self, client, team, admin):
        FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        export_id = _run_task(team)
        File.objects.get(id=export_id).delete()
        client.force_login(admin)
        response = client.get(_manage_team_url(team))
        content = response.content.decode()
        assert "ocs-ready-download-link" not in content
        assert "ocs-download-progress-root" not in content
