import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.custom_actions.models import CustomActionOperation
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.custom_actions import CustomActionFactory, CustomActionOperationFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory

_migration = importlib.import_module("apps.assistants.migrations.0016_delete_assistant_data")
delete_assistant_data = _migration.delete_assistant_data


class FakeSchemaEditor:
    """Stands in for the schema editor a RunPython operation receives."""

    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _state():
    """The app state the migration actually receives, not the live registry.

    Built from the migration's own dependency list so the two cannot drift: a dependency that is
    missing here resolves other apps to a stale state whose columns no longer exist in the DB.
    These models still carry the assistant FKs, which the live ones have already dropped from
    state while the columns remain -- so this is also how a test sets up pre-migration rows.
    """
    return MigrationLoader(None).project_state(_migration.Migration.dependencies).apps


def _run():
    delete_assistant_data(_state(), FakeSchemaEditor())


@pytest.mark.django_db()
def test_deletes_assistants_and_their_tool_resources():
    assistant = OpenAiAssistantFactory.create()
    resource = ToolResources.objects.create(assistant=assistant, tool_type="file_search")
    resource.files.add(FileFactory.create(team=assistant.team))

    _run()

    assert not OpenAiAssistant.objects.get_all().filter(pk=assistant.pk).exists()
    assert not ToolResources.objects.filter(pk=resource.pk).exists()


@pytest.mark.django_db()
def test_deletes_assistant_attached_operations_and_keeps_node_attached_ones():
    """CustomActionOperation.assistant is CASCADE, so the assistant delete takes those rows."""
    assistant = OpenAiAssistantFactory.create()
    action = CustomActionFactory.create(team=assistant.team)
    assistant_op = (
        _state()
        .get_model("custom_actions", "CustomActionOperation")
        .objects.create(assistant_id=assistant.id, custom_action_id=action.id, operation_id="weather_get")
    )
    node_op = CustomActionOperationFactory.create(custom_action=action)

    _run()

    assert not CustomActionOperation.objects.filter(pk=assistant_op.pk).exists()
    assert CustomActionOperation.objects.filter(pk=node_op.pk).exists()


@pytest.mark.django_db()
def test_nulls_the_node_fk_and_strips_the_param():
    assistant = OpenAiAssistantFactory.create()
    node = NodeFactory.create(
        type="AssistantNode",
        params={"name": "assist", "assistant_id": str(assistant.id), "citations_enabled": True},
    )
    historical_node = _state().get_model("pipelines", "Node")
    historical_node.objects.filter(pk=node.pk).update(assistant_id=assistant.id)

    _run()

    node.refresh_from_db()
    assert historical_node.objects.get(pk=node.pk).assistant_id is None
    assert node.params == {"name": "assist", "citations_enabled": True}


@pytest.mark.django_db()
def test_leaves_params_without_the_key_untouched():
    node = NodeFactory.create(type="LLMResponseWithPrompt", params={"name": "llm", "prompt": "hi"})

    _run()

    node.refresh_from_db()
    assert node.params == {"name": "llm", "prompt": "hi"}


@pytest.mark.django_db()
def test_is_a_noop_when_there_are_no_assistants():
    node = NodeFactory.create(type="Passthrough", params={"name": "pass"})

    _run()

    node.refresh_from_db()
    assert node.params == {"name": "pass"}
    assert not OpenAiAssistant.objects.get_all().exists()
