import pytest
from django.core.management import call_command

from apps.files.models import File, FilePurpose
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory, DocumentSourceFactory
from apps.utils.factories.experiment import ChatAttachmentFactory
from apps.utils.factories.files import FileFactory

# The literal, not FilePurpose.ASSISTANT: the enum member is removed later in this same PR and
# these tests must keep passing afterwards.
ASSISTANT = "assistant"


def _run(**kwargs):
    call_command("retire_assistant_file_purpose", **kwargs)


@pytest.mark.django_db()
def test_deletes_an_unreferenced_assistant_file():
    orphan = FileFactory.create(purpose=ASSISTANT)

    _run()

    assert not File.objects.get_all().filter(pk=orphan.pk).exists()


@pytest.mark.django_db()
def test_repoints_a_file_that_is_now_in_a_collection():
    """The create-from-assistant flow linked existing files into collections without repurposing them."""
    collection_file = CollectionFileFactory.create()
    File.objects.filter(pk=collection_file.file_id).update(purpose=ASSISTANT)

    _run()

    collection_file.file.refresh_from_db()
    assert collection_file.file.purpose == FilePurpose.COLLECTION


@pytest.mark.django_db()
def test_repoints_a_file_that_is_now_a_document_source():
    """DocumentSource.files runs through CollectionFile, so document_source is set on the row."""
    source = DocumentSourceFactory.create()
    collection_file = CollectionFileFactory.create(collection=source.collection, document_source=source)
    File.objects.filter(pk=collection_file.file_id).update(purpose=ASSISTANT)

    _run()

    collection_file.file.refresh_from_db()
    assert collection_file.file.purpose == FilePurpose.COLLECTION


@pytest.mark.django_db()
def test_repoints_a_file_that_is_now_a_chat_attachment():
    attachment = ChatAttachmentFactory.create(tool_type="ocs_attachments")
    file = FileFactory.create(team=attachment.chat.team, purpose=ASSISTANT)
    attachment.files.add(file)

    _run()

    file.refresh_from_db()
    assert file.purpose == FilePurpose.MESSAGE_MEDIA


@pytest.mark.django_db()
def test_a_collection_reference_wins_over_a_chat_attachment():
    """Mirrors backfill_file_purpose's precedence: COLLECTION is checked before MESSAGE_MEDIA."""
    collection_file = CollectionFileFactory.create()
    attachment = ChatAttachmentFactory.create(tool_type="ocs_attachments")
    attachment.files.add(collection_file.file)
    File.objects.filter(pk=collection_file.file_id).update(purpose=ASSISTANT)

    _run()

    collection_file.file.refresh_from_db()
    assert collection_file.file.purpose == FilePurpose.COLLECTION


@pytest.mark.django_db()
def test_leaves_an_archived_assistant_file_alone():
    """Archiving is how the app deliberately retains a file after its references go."""
    archived = FileFactory.create(purpose=ASSISTANT, is_archived=True)

    _run()

    archived.refresh_from_db()
    assert archived.purpose == ASSISTANT


@pytest.mark.django_db()
def test_leaves_a_versioned_assistant_file_alone():
    """A version relationship is a live reference the repoint rules cannot classify."""
    working = FileFactory.create(purpose=ASSISTANT)
    version = FileFactory.create(purpose=ASSISTANT, team=working.team, working_version=working)

    _run()

    for file in (working, version):
        file.refresh_from_db()
        assert file.purpose == ASSISTANT


@pytest.mark.django_db()
def test_leaves_other_purposes_alone():
    media = FileFactory.create(purpose=FilePurpose.MESSAGE_MEDIA)
    export = FileFactory.create(purpose=FilePurpose.DATA_EXPORT)
    CollectionFactory.create()

    _run()

    for file in (media, export):
        file.refresh_from_db()
    assert media.purpose == FilePurpose.MESSAGE_MEDIA
    assert export.purpose == FilePurpose.DATA_EXPORT


@pytest.mark.django_db()
def test_dry_run_changes_nothing():
    orphan = FileFactory.create(purpose=ASSISTANT)
    collection_file = CollectionFileFactory.create()
    File.objects.filter(pk=collection_file.file_id).update(purpose=ASSISTANT)

    _run(dry_run=True)

    assert File.objects.get_all().filter(pk=orphan.pk).exists()
    collection_file.file.refresh_from_db()
    assert collection_file.file.purpose == ASSISTANT


@pytest.mark.django_db()
def test_a_second_run_is_a_noop():
    """It is an IdempotentCommand: the run-once slug blocks the second invocation."""
    orphan = FileFactory.create(purpose=ASSISTANT)

    _run()
    _run()

    assert not File.objects.get_all().filter(pk=orphan.pk).exists()
