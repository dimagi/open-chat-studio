import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.assistants.models import ToolResources
from apps.files.models import File, FilePurpose
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFileFactory
from apps.utils.factories.experiment import ChatAttachmentFactory
from apps.utils.factories.files import FileFactory


def _run():
    call_command("backfill_file_purpose")


@pytest.mark.django_db()
def test_backfill_assigns_purpose_by_relation_and_pattern():
    collection_file = CollectionFileFactory.create()

    assistant = OpenAiAssistantFactory.create()
    tool_resource = ToolResources.objects.create(assistant=assistant, tool_type="code_interpreter")
    tool_file = FileFactory.create(team=assistant.team)
    tool_resource.files.add(tool_file)

    voice_attachment = ChatAttachmentFactory.create(tool_type="voice_message")
    voice_file = FileFactory.create(team=voice_attachment.chat.team)
    voice_attachment.files.add(voice_file)

    code_attachment = ChatAttachmentFactory.create(tool_type="code_interpreter")
    code_file = FileFactory.create(team=code_attachment.chat.team)
    code_attachment.files.add(code_file)

    openai_file = FileFactory.create(external_source="openai")
    gzip_export = FileFactory.create(content_type="application/gzip", name="My Bot Chat Export 2026-06-30.csv.gz")
    eval_export = FileFactory.create(content_type="text/csv", name="config_latest_results_2026-06-30.csv")
    zip_export = FileFactory.create(content_type="application/zip", name="my-collection_files_20260630.zip")

    _run()

    assert File.objects.get(pk=collection_file.file.pk).purpose == FilePurpose.COLLECTION
    assert File.objects.get(pk=tool_file.pk).purpose == FilePurpose.ASSISTANT
    assert File.objects.get(pk=voice_file.pk).purpose == FilePurpose.MESSAGE_MEDIA
    assert File.objects.get(pk=code_file.pk).purpose == FilePurpose.ASSISTANT
    assert File.objects.get(pk=openai_file.pk).purpose == FilePurpose.ASSISTANT
    assert File.objects.get(pk=gzip_export.pk).purpose == FilePurpose.DATA_EXPORT
    assert File.objects.get(pk=eval_export.pk).purpose == FilePurpose.DATA_EXPORT
    assert File.objects.get(pk=zip_export.pk).purpose == FilePurpose.DATA_EXPORT


@pytest.mark.django_db()
def test_backfill_sets_expiry_on_exports_relative_to_creation():
    export = FileFactory.create(content_type="application/gzip", name="X Chat Export.csv.gz")
    assert export.expiry_date is None

    _run()

    export.refresh_from_db()
    assert export.expiry_date is not None
    # 7 days after creation, give or take clock skew during the test
    expected = export.created_at + timezone.timedelta(days=7)
    assert abs((export.expiry_date - expected).total_seconds()) < 60


@pytest.mark.django_db()
def test_backfill_leaves_ambiguous_and_already_set_files_untouched():
    ambiguous = FileFactory.create(content_type="text/plain")
    ocs_attachment = ChatAttachmentFactory.create(tool_type="ocs_attachments")
    ocs_file = FileFactory.create(team=ocs_attachment.chat.team)
    ocs_attachment.files.add(ocs_file)

    already_set = FileFactory.create(external_source="openai", purpose=FilePurpose.MESSAGE_MEDIA)

    _run()

    assert File.objects.get(pk=ambiguous.pk).purpose == ""
    assert File.objects.get(pk=ocs_file.pk).purpose == ""
    # an explicit purpose is never overwritten, even when a rule would match
    assert File.objects.get(pk=already_set.pk).purpose == FilePurpose.MESSAGE_MEDIA


@pytest.mark.django_db()
def test_backfill_is_idempotent():
    openai_file = FileFactory.create(external_source="openai")

    _run()
    # second run is a no-op (migration already applied); purpose stays correct
    call_command("backfill_file_purpose", "--force")

    assert File.objects.get(pk=openai_file.pk).purpose == FilePurpose.ASSISTANT
