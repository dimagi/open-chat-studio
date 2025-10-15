import pathlib

import pytest
from django.core.files.base import ContentFile

from apps.documents.readers import Document
from apps.files.models import File

BASE_PATH = pathlib.Path(__file__).parent / "data"


@pytest.mark.parametrize(
    ("filename", "expected_content", "part_count"),
    [
        pytest.param("test.pdf", "PDF documents can be\n\n\x0chard to read 🫠\n\n", 1, id="pdf"),
        pytest.param("test.txt", "Hi\n\nHere is a text file with 🥰 emoji.\n", 1, id="txt"),
        pytest.param("test.docx", "doc, but with an x 😊", 1, id="docx"),
    ],
)
def test_document(filename, expected_content, part_count):
    content_type = {
        "pdf": "application/pdf",
        "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[filename.split(".")[-1]]
    with pathlib.Path(BASE_PATH / filename).open("rb") as f:
        file = File(name=filename, team_id=1, content_type=content_type, file=ContentFile(f.read(), name=filename))

    doc = Document.from_file(file)
    assert doc.get_contents_as_string() == expected_content
    assert len(doc.parts) == part_count
