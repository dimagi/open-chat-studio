import pytest
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.annotations.models import Tag
from apps.documents.models import DocumentSource
from apps.evaluations.models import EvaluatorTagRule
from apps.service_providers.models import AuthProvider
from apps.utils.deletion import _deletion_order, delete_object_with_auditing_of_related_objects
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import DocumentSourceFactory
from apps.utils.factories.evaluations import EvaluatorTagRuleFactory
from apps.utils.factories.service_provider_factories import AuthProviderFactory, LlmProviderFactory
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
    from apps.utils.tests.models import (  # noqa: PLC0415 - lazy: test models require setup_test_app() to run first; moving to module level would import before app tables are created
        MODEL_NAMES,
        Bot,
        Collection,
        Tool,
    )

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
        team = TeamFactory.create()
        assistant = OpenAiAssistantFactory.create(llm_provider=LlmProviderFactory.create(team=team), team=team)

        team_to_delete = TeamFactory.create()
        LlmProviderFactory.create(team=team_to_delete)

        delete_object_with_auditing_of_related_objects(team_to_delete)
        assistant.refresh_from_db()
        assert assistant.llm_provider is not None


@pytest.mark.django_db()
def test_deleting_a_team_with_protected_fk_between_owned_objects():
    """Deleting a team must not fail when one team-owned object PROTECT-references another.

    A DocumentSource has a PROTECT FK to an AuthProvider. Both belong to the team, so both are
    deleted, but the DocumentSource must be deleted first -- otherwise deleting the AuthProvider
    raises ProtectedError.
    """
    with enable_audit():
        team = TeamFactory.create()
        auth_provider = AuthProviderFactory.create(team=team)
        DocumentSourceFactory.create(team=team, collection__team=team, auth_provider=auth_provider)

        delete_object_with_auditing_of_related_objects(team)

        assert not DocumentSource.objects.filter(team=team).exists()
        assert not AuthProvider.objects.filter(team=team).exists()


@pytest.mark.django_db()
def test_deleting_a_team_with_protected_tag_reference():
    """Same ordering guarantee for the evaluations EvaluatorTagRule -> Tag PROTECT FK."""
    with enable_audit():
        team = TeamFactory.create()
        EvaluatorTagRuleFactory.create(team=team)

        delete_object_with_auditing_of_related_objects(team)

        assert not EvaluatorTagRule.objects.filter(team=team).exists()
        assert not Tag.objects.filter(team=team).exists()


def test_deletion_order_keeps_base_order_without_protect_constraints():
    from apps.utils.tests.models import (  # noqa: PLC0415 - lazy: test models require setup_test_app() to run first
        Collection,
        Param,
        Tool,
    )

    assert _deletion_order({Collection: [], Tool: [], Param: []}) == [Param, Tool, Collection]


def test_deletion_order_moves_protect_referencer_first():
    from apps.utils.tests.models import (  # noqa: PLC0415 - lazy: test models require setup_test_app() to run first
        Bot,
        Webhook,
    )

    # base order [Bot, Webhook] but Webhook PROTECT-references Bot
    assert _deletion_order({Webhook: [], Bot: []}) == [Webhook, Bot]


def test_deletion_order_does_not_leapfrog_cascade_references():
    """A PROTECT-delayed model must not be leapfrogged past a model it CASCADE-references.

    Base order [Bot, Collection, Webhook]. Webhook PROTECT-references Bot, delaying Bot. Bot
    CASCADE-references Collection, so Collection must still be deleted after Bot -- deleting
    Collection first would cascade-delete Bot's rows outside the audited per-model delete.
    """
    from apps.utils.tests.models import (  # noqa: PLC0415 - lazy: test models require setup_test_app() to run first
        Bot,
        Collection,
        Webhook,
    )

    order = _deletion_order({Webhook: [], Collection: [], Bot: []})
    assert order.index(Webhook) < order.index(Bot) < order.index(Collection)


def test_deletion_order_protect_cycle_falls_back_to_base_order():
    from apps.utils.tests.models import (  # noqa: PLC0415 - lazy: test models require setup_test_app() to run first
        CycleA,
        CycleB,
    )

    assert _deletion_order({CycleA: [], CycleB: []}) == [CycleB, CycleA]
