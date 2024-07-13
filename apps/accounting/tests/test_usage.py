from unittest.mock import Mock

import pytest

from apps.accounting.models import Usage
from apps.accounting.usage import UsageOutOfScopeError, UsageRecorder, UsageType
from apps.chat.models import Chat
from apps.service_providers.models import LlmProvider


@pytest.fixture()
def llm_provider():
    return LlmProvider(
        team_id=123,
        name="test",
        type="openai",
        config={
            "openai_api_key": "123123123",
        },
    )


@pytest.fixture()
def source_object():
    return Chat(id=1, team_id=123)


@pytest.fixture()
def recorder(llm_provider):
    return UsageRecorder(llm_provider)


@pytest.fixture()
def _db_test(team_with_users, llm_provider, source_object):
    """For tests that require the DB, we need to create the objects in the DB."""
    llm_provider.id = None
    llm_provider.team = team_with_users
    llm_provider.save()
    source_object.id = None
    source_object.team = team_with_users
    source_object.save()


def test_record_usage_no_scope_raises_error(recorder):
    with pytest.raises(UsageOutOfScopeError):
        recorder.record_usage(UsageType.INPUT_TOKENS, 100)


@pytest.mark.parametrize("metadata", [None, {}, {"key": "value"}])
def test_get_current_scope(recorder, llm_provider, source_object, metadata):
    with recorder.for_source(source_object, metadata):
        scope = recorder.get_current_scope()
        assert scope.service_object == llm_provider
        assert scope.source_object == source_object
        assert scope.metadata == (metadata or {})


@pytest.mark.django_db()
@pytest.mark.usefixtures("_db_test")
def test_record_usage(recorder, source_object):
    metadata = {"key": "value"}
    with recorder.for_source(source_object, metadata=metadata):
        recorder.record_usage(UsageType.INPUT_TOKENS, 100)
        recorder.record_usage(UsageType.OUTPUT_TOKENS, 5)
        assert len(recorder.usage) == 2
    assert len(recorder.usage) == 0
    assert not recorder.scope
    assert list(Usage.objects.values_list("type", "value", "metadata")) == [
        (UsageType.INPUT_TOKENS, 100, metadata),
        (UsageType.OUTPUT_TOKENS, 5, metadata),
    ]


def test_record_usage_merge_multiple(recorder, source_object):
    recorder.commit_and_clear = Mock()
    with recorder.for_source(source_object):
        recorder.record_usage(UsageType.INPUT_TOKENS, 7)
        recorder.record_usage(UsageType.INPUT_TOKENS, 5)
        recorder.record_usage(UsageType.OUTPUT_TOKENS, 9)

        with recorder.update_metadata({"key": "value"}):
            recorder.record_usage(UsageType.INPUT_TOKENS, 2)
            recorder.record_usage(UsageType.OUTPUT_TOKENS, 3)

        recorder.record_usage(UsageType.INPUT_TOKENS, 4, metadata={"key": "value"})
        recorder.record_usage(UsageType.INPUT_TOKENS, 5, metadata={"key": "value1"})

        usages = recorder.get_batch()
    assert [(usage.type, usage.value, usage.metadata) for usage in usages] == [
        (UsageType.INPUT_TOKENS, 12, {}),
        (UsageType.OUTPUT_TOKENS, 9, {}),
        (UsageType.INPUT_TOKENS, 6, {"key": "value"}),
        (UsageType.OUTPUT_TOKENS, 3, {"key": "value"}),
        (UsageType.INPUT_TOKENS, 5, {"key": "value1"}),
    ]


def test_update_metadata_context_manager(recorder, llm_provider, source_object):
    # Test the update_metadata context manager
    with recorder.for_source(source_object, metadata={"key": "1"}):
        assert len(recorder.scope) == 1
        assert recorder.scope[0].source_object == source_object
        assert recorder.scope[0].service_object == llm_provider
        assert recorder.scope[0].metadata == {"key": "1"}

        # Update metadata
        with recorder.update_metadata({"key": "2", "key2": "3"}):
            assert len(recorder.scope) == 2
            assert recorder.scope[1].metadata == {"key": "2", "key2": "3"}

        # After exiting the context, the scope should be back to its original state
        assert len(recorder.scope) == 1
        assert recorder.scope[0].metadata == {"key": "1"}

    # After exiting the for_source context, the scope should be empty
    assert len(recorder.scope) == 0


@pytest.fixture()
def another_source_object():
    return Chat(id=2, team_id=123)


def test_nested_for_source_context_manager(recorder, llm_provider, source_object, another_source_object):
    # Test the for_source context manager with nested calls
    with recorder.for_source(source_object):
        assert len(recorder.scope) == 1
        assert recorder.scope[0].source_object == source_object
        assert recorder.scope[0].service_object == llm_provider

        with recorder.for_source(another_source_object):
            assert len(recorder.scope) == 2
            assert recorder.scope[1].source_object == another_source_object
            assert recorder.scope[1].service_object == llm_provider

        # After exiting the inner context, the scope should be back to its original state
        assert len(recorder.scope) == 1
        assert recorder.scope[0].source_object == source_object
        assert recorder.scope[0].service_object == llm_provider

    # After exiting the outer context, the scope should be empty
    assert len(recorder.scope) == 0
