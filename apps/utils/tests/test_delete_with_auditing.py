import pytest
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.utils.deletion import delete_object_with_auditing_of_related_objects
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
@pytest.mark.usefixtures("_model_setup")
@pytest.mark.parametrize(
    ("obj_name", "delete_events", "update_events", "expected_stats"),
    [
        ("b1", ["Bot b1"], [], {"Bot": 1, "Bot_tools": 2}),
        ("t1", ["Tool t1"], [], {"Tool": 1, "Param": 2, "Bot_tools": 2}),
        ("c1", ["Collection c1", "Bot b1", "Bot b2"], ["Tool-collection"], {"Collection": 1, "Bot": 2, "Bot_tools": 3}),
        ("c2", ["Collection c2", "Bot b3"], ["Tool-collection"], {"Collection": 1, "Bot": 1}),
    ],
)
def test_delete_with_auditing(obj_name, delete_events, update_events, expected_stats):
    # inline import to avoid importing before app initialization
    from apps.utils.tests.models import MODEL_NAMES, Bot, Collection, Tool

    with enable_audit():
        source_model = {
            "b": Bot,
            "t": Tool,
            "c": Collection,
        }[obj_name[0]]
        source = source_model.objects.get(name=obj_name)
        stats = delete_object_with_auditing_of_related_objects(source)
        norm_stats = {model.split(".")[-1]: count for model, count in stats.items()}
        assert norm_stats == expected_stats

        actual_delete_events = {
            f"{e.object_class_path.split('.')[-1]} {e.delta['name']['old']}"
            for e in AuditEvent.objects.filter(is_delete=True)
        }
        assert actual_delete_events == set(delete_events)

        actual_update_events = {
            f"{e.object_class_path.split('.')[-1]}-{','.join(e.delta)}"
            for e in AuditEvent.objects.filter(is_delete=False, is_create=False, object_class_path__in=MODEL_NAMES)
        }
        assert actual_update_events == set(update_events)


@pytest.mark.django_db()
def test_deleting_a_team_does_not_remove_llm_providers_from_other_teams():
    """
    There was an issue where if you remove a team that has an LLMProvider, it would clear the LLMProvider FKs from
    some assistants that were associated with other teams. This test ensures that this issue is fixed.
    """
    with enable_audit():
        team = TeamFactory()
        assistant = OpenAiAssistantFactory(llm_provider=LlmProviderFactory(team=team), team=team)

        team_to_delete = TeamFactory()
        LlmProviderFactory(team=team_to_delete)

        delete_object_with_auditing_of_related_objects(team_to_delete)
        assistant.refresh_from_db()
        assert assistant.llm_provider is not None
