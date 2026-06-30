import io
import zipfile

import pytest
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.files.models import File, FilePurpose
from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.file_export import stream_team_files_zip
from apps.teams.forms import TeamPublicKeyForm
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.factories.files import FileFactory

PUBLIC_KEY = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv6uzoOlzcQBMGnCY0Vi0
pL6G/Gep6ayMlqNJoHpvGneiXWyVGeSQoTyopLh+jP8tDR82ZxK42ropGLKAS8Gs
X1TnfK41a+sa0Linjhe8I6OdlmiaxgUjRbM1eTfS9Irw/IkayDj+XbBrNrFXzxsN
WDVkN6gLPlA6Vh9dry/BonJz3oHV32amRmDVOm+ddeMRDODTZB2DiBwkWQZeRT2C
lGELhjr3RXNLsLEg86o2Qx0isKXq5BNOWP6rkKJWn6KFZoQLpWM+8X/t0ofUD76l
/USJWY8AmN90ov+OtxBB5UBMfZeGVYxYLZju/r2i4iglrai6cB1QbjntzeitiDa4
KwIDAQAB
-----END PUBLIC KEY-----"""


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


def _set_public_key_url(team):
    return reverse("single_team:set_public_key", args=[team.slug])


def _manage_team_url(team):
    return reverse("single_team:manage_team", args=[team.slug])


@pytest.mark.django_db()
class TestSetPublicKey:
    def test_form_accepts_public_key(self):
        form = TeamPublicKeyForm(data={"public_key": PUBLIC_KEY})
        assert form.is_valid(), form.errors

    def test_form_rejects_invalid_public_key(self):
        form = TeamPublicKeyForm(data={"public_key": "not-a-real-key"})
        assert not form.is_valid()
        assert "public_key" in form.errors

    def test_admin_can_set_public_key(self, client, team, admin):
        client.force_login(admin)
        response = client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        assert response.status_code == 200
        team.refresh_from_db()
        assert team.public_key == PUBLIC_KEY

    def test_member_cannot_set_public_key(self, client, team, member):
        client.force_login(member)
        response = client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        assert response.status_code == 403
        team.refresh_from_db()
        assert team.public_key == ""

    def test_setting_invalid_public_key_shows_form_error(self, client, team, admin):
        client.force_login(admin)
        response = client.post(_set_public_key_url(team), {"public_key": "not-a-real-key"})
        assert response.status_code == 200
        form = response.context["public_key_form"]
        assert "public_key" in form.errors
        team.refresh_from_db()
        assert team.public_key == ""

    def test_data_export_section_visible_to_admin(self, client, team, admin):
        client.force_login(admin)
        response = client.get(_manage_team_url(team))
        assert response.status_code == 200
        assert "Data Export" in response.content.decode()

    def test_data_export_section_hidden_from_member(self, client, team, member):
        client.force_login(member)
        response = client.get(_manage_team_url(team))
        assert response.status_code == 200
        assert "Data Export" not in response.content.decode()

    def test_setting_public_key_is_audited(self, client, team, admin):
        client.force_login(admin)
        with enable_audit():
            client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
        assert any("public_key" in (event.delta or {}) for event in events)


def _zip_from_stream(team):
    body = b"".join(stream_team_files_zip(team))
    return zipfile.ZipFile(io.BytesIO(body))


@pytest.mark.django_db()
class TestStreamTeamFilesZip:
    def test_includes_team_working_files(self, team):
        f1 = FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        f2 = FileFactory(team=team, file__data=b"world", file__filename="b.txt")
        zf = _zip_from_stream(team)
        assert set(zf.namelist()) == {f1.file.name, f2.file.name}
        assert zf.read(f1.file.name) == b"hello"
        assert zf.read(f2.file.name) == b"world"
        assert zf.testzip() is None

    def test_excludes_archived_files(self, team):
        keep = FileFactory(team=team, file__data=b"x", file__filename="keep.txt")
        FileFactory(team=team, is_archived=True, file__data=b"y", file__filename="drop.txt")
        assert _zip_from_stream(team).namelist() == [keep.file.name]

    def test_excludes_versioned_copies(self, team):
        working = FileFactory(team=team, file__data=b"w", file__filename="w.txt")
        FileFactory(team=team, working_version=working, file__data=b"v", file__filename="v.txt")
        assert _zip_from_stream(team).namelist() == [working.file.name]

    def test_excludes_data_export_artifacts(self, team):
        keep = FileFactory(team=team, file__data=b"x", file__filename="keep.txt")
        FileFactory(
            team=team,
            purpose=FilePurpose.DATA_EXPORT,
            file__data=b"z",
            file__filename="prev-export.zip",
        )
        assert _zip_from_stream(team).namelist() == [keep.file.name]

    def test_excludes_other_teams_files(self, team):
        mine = FileFactory(team=team, file__data=b"mine", file__filename="mine.txt")
        other = Team.objects.create(name="Other", slug="other")
        FileFactory(team=other, file__data=b"theirs", file__filename="theirs.txt")
        assert _zip_from_stream(team).namelist() == [mine.file.name]

    def test_skips_files_that_cannot_be_read(self, team):
        present = FileFactory(team=team, file__data=b"present", file__filename="present.txt")
        gone = FileFactory(team=team, file__data=b"gone", file__filename="gone.txt")
        gone.file.storage.delete(gone.file.name)  # row points at a file missing from storage
        File.objects.create(team=team, name="external-only")  # row with an empty file field
        assert _zip_from_stream(team).namelist() == [present.file.name]

    def test_empty_team_produces_valid_empty_zip(self, team):
        zf = _zip_from_stream(team)
        assert zf.namelist() == []
        assert zf.testzip() is None


def _download_files_url(team):
    return reverse("single_team:download_team_files", args=[team.slug])


@pytest.mark.django_db()
class TestDownloadTeamFiles:
    def test_admin_downloads_zip(self, client, team, admin):
        f = FileFactory(team=team, file__data=b"hello", file__filename="a.txt")
        client.force_login(admin)
        response = client.get(_download_files_url(team))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        assert f"team-{team.slug}-files-" in response["Content-Disposition"]
        body = b"".join(response.streaming_content)
        zf = zipfile.ZipFile(io.BytesIO(body))
        assert zf.read(f.file.name) == b"hello"

    def test_member_forbidden(self, client, team, member):
        client.force_login(member)
        response = client.get(_download_files_url(team))
        assert response.status_code == 403


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
