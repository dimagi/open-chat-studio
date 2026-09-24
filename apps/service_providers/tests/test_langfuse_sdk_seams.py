"""Guards on the Langfuse internals OCS still depends on.

The SDK offers no public equivalent for any of them, so these assert the symbols are still
there. An SDK upgrade that removes one fails here instead of silently breaking tracing.
"""

from langfuse._client.resource_manager import LangfuseResourceManager
from langfuse._utils.prompt_cache import PromptCache
from langfuse.langchain import CallbackHandler


def test_sdk_registry_seam_exists():
    """`_detach_sdk_resources` needs these to retire a cached client -- see its docstring."""
    assert isinstance(LangfuseResourceManager._instances, dict)
    assert hasattr(LangfuseResourceManager._lock, "acquire")
    assert callable(LangfuseResourceManager.reset)


def test_langchain_parent_observation_seam_exists():
    """`LangfuseCallbackHandler.on_custom_event` parents events through this lookup."""
    assert callable(getattr(CallbackHandler, "_get_parent_observation", None))


def test_sdk_prompt_cache_seam_exists():
    """`_shutdown_detached` stops a retired client's prompt cache refresh thread through this."""
    PromptCache()._task_manager.shutdown()
