import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.files.models import MAX_STORED_FILENAME_LENGTH, File, file_upload_to
from apps.utils.factories.team import TeamFactory

LONG_MULTI_DOT_NAME = (
    "V.R_ValidC3A9_Gabon_JRSM2027_v44L_espen_12022026_2027_05062026_1778073352_2027_08212026_1787295174.xlsm"
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        pytest.param("report.pdf", "report.pdf", id="plain"),
        pytest.param("archive.tar.gz", "archive_tar.gz", id="multi-dot"),
        pytest.param("README", "README", id="no-extension"),
        pytest.param(
            LONG_MULTI_DOT_NAME,
            "V_R_ValidC3A9_Gabon_JRSM2027_v44L_espen_12022026_2027_05062026_1778073352_2027_08212026_1787295174.xlsm",
            id="long-multi-dot",
        ),
    ],
)
def test_file_upload_to(filename, expected):
    assert file_upload_to(None, filename) == expected


def test_file_upload_to_caps_length():
    name = file_upload_to(None, "a" * 400 + ".xlsm")
    assert len(name) == MAX_STORED_FILENAME_LENGTH
    assert name.endswith(".xlsm")


def test_file_upload_to_bogus_long_extension():
    name = file_upload_to(None, "V." + "x" * 300)
    assert len(name) <= MAX_STORED_FILENAME_LENGTH
    assert "." not in name


@pytest.mark.django_db()
def test_save_file_with_long_multi_dot_name():
    team = TeamFactory.create()
    upload = SimpleUploadedFile(LONG_MULTI_DOT_NAME, b"data", content_type="application/octet-stream")
    file_obj = File.objects.create(team=team, name=upload.name, file=upload)
    # A second upload with the same name must also succeed (Django adds a random suffix).
    upload2 = SimpleUploadedFile(LONG_MULTI_DOT_NAME, b"data", content_type="application/octet-stream")
    file_obj2 = File.objects.create(team=team, name=upload2.name, file=upload2)

    assert file_obj.name == LONG_MULTI_DOT_NAME
    assert file_obj.file.name.endswith(".xlsm")
    assert file_obj2.file.name != file_obj.file.name
