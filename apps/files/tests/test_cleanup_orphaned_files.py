import pytest
from django.core.management import call_command

from apps.assistants.models import ToolResources
from apps.files.models import File, FilePurpose
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFileFactory
from apps.utils.factories.experiment import ChatAttachmentFactory, SyntheticVoiceFactory
from apps.utils.factories.files import FileFactory


def _run():
    call_command("cleanup_orphaned_files")


@pytest.mark.django_db()
def test_deletes_unreferenced_legacy_files():
    orphan = FileFactory.create(name="clv_source_material.txt", content_type="text/plain")

    _run()

    assert not File.objects.filter(pk=orphan.pk).exists()


@pytest.mark.django_db()
def test_keeps_referenced_and_special_files():
    collection_file = CollectionFileFactory.create()

    assistant = OpenAiAssistantFactory.create()
    tool_resource = ToolResources.objects.create(assistant=assistant, tool_type="code_interpreter")
    tool_file = FileFactory.create(team=assistant.team)
    tool_resource.files.add(tool_file)

    attachment = ChatAttachmentFactory.create(tool_type="ocs_attachments")
    attached_file = FileFactory.create(team=attachment.chat.team)
    attachment.files.add(attached_file)

    voice_file = FileFactory.create()
    SyntheticVoiceFactory.create(file=voice_file)

    openai_file = FileFactory.create(external_source="openai")
    export = FileFactory.create(content_type="text/csv", name="My Bot Chat Export 2025-08-21.csv")
    classified = FileFactory.create(purpose=FilePurpose.MESSAGE_MEDIA)

    _run()

    for kept in [collection_file.file, tool_file, attached_file, voice_file, openai_file, export, classified]:
        assert File.objects.filter(pk=kept.pk).exists(), kept.name


@pytest.mark.django_db()
def test_keeps_versioned_files():
    working = FileFactory.create()
    version = working.create_new_version()

    _run()

    assert File.objects.filter(pk=working.pk).exists()
    assert File.objects.filter(pk=version.pk).exists()


@pytest.mark.django_db()
def test_dry_run_deletes_nothing():
    orphan = FileFactory.create()

    call_command("cleanup_orphaned_files", "--dry-run")

    assert File.objects.filter(pk=orphan.pk).exists()
