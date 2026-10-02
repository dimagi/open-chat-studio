import pytest

from apps.files.models import File
from apps.utils.factories.experiment import ChatAttachmentFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def attachment():
    attachment = ChatAttachmentFactory.create(tool_type="code_interpreter")
    attachment.files.set(FileFactory.create_batch(3, team=attachment.chat.team))
    return attachment


@pytest.mark.django_db()
def test_deleting_attachment_deletes_its_files(attachment):
    attachment.delete()

    assert File.objects.count() == 0


@pytest.mark.django_db()
def test_deleting_attachment_keeps_files_another_object_references(attachment, caplog):
    shared = attachment.files.first()
    other = ChatAttachmentFactory.create(chat=attachment.chat, tool_type="file_search")
    other.files.set([shared])

    attachment.delete()

    assert list(File.objects.all()) == [shared]
    assert str(shared.id) in caplog.text
