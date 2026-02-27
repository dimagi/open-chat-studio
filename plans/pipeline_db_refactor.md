# Refactor 2: PipelineRepository — Centralized DB Access for Pipeline Execution

## Context

This is the reviewed and updated version of `refactor-plan-pipeline-repository.md`. Changes from the original plan are based on the interactive review (14 issues across Architecture, Code Quality, Tests, and Performance).

Key changes from original:

- Renamed `DjangoPipelineRepository` → `ORMRepository`
- Changed `PipelineRepository` from `Protocol` to ABC with `@abstractmethod`
- Added `RepositoryLookupError` exception for consistent error handling
- Added repo methods for 100% DB access coverage (`get_llm_service`, `get_collection_file_info`, `get_participant_global_data`)
- Fixed `save_compression_checkpoint` implementation bug (missing summary branch)
- `PromptTemplateContext` gets optional `repo=None` with direct ORM fallback
- `InMemoryPipelineRepository` uses `factory_boy` `.build()` instead of `SimpleNamespace`
- Protocol return types use `list`/`Sequence`/`NamedTuple` instead of `QuerySet`
- `PipelineTestBot` fix moved to start of Phase 2
- Each Phase 2 step includes corresponding test updates
- Added shared parametrized test suite and injection wiring test in Phase 1

## Problem

Database access during pipeline execution is scattered across nodes, mixins, and finalization code with no consistent pattern. Nodes reach through `ExperimentSession` to query related models (`session.chat.get_langchain_messages`, `session.pipeline_chat_history.get_or_create`), call ORM directly (`LlmProvider.objects.get`, `Collection.objects.filter`, `File.create`), and it's not clear to readers where or when the persistence layer is touched. This makes:

- **Testing hard** — most node tests require `@pytest.mark.django_db` even for pure logic tests
- **Auditing hard** — no single place to see all DB operations during pipeline execution
- **Optimization hard** — can't easily add caching, batching, or query analysis

## Current DB Access Points

| Location | Operations | Notes |
| :---- | :---- | :---- |
| `mixins.py` HistoryMixin | `session.chat.get_langchain_messages_until_marker()`, `session.pipeline_chat_history.get()`, `session.pipeline_chat_history.get_or_create()`, `history.messages.create()`, `ChatMessage.objects.get()`, `PipelineChatMessages.objects.filter().update()` |  |
| `mixins.py` LLMResponseMixin | `LlmProvider.objects.get()` (in `get_llm_service()`), then `provider.get_llm_service()` to create service | Two ops: fetch \+ service creation |
| `mixins.py` module-level | `get_llm_provider_model()` (lru\_cached), `get_llm_provider()` (lru\_cached) | Used in validators — out of scope |
| `llm_node.py` `_get_search_tool` | `Collection.objects.filter()` |  |
| `llm_node.py` `_process_files` | `session.chat.attach_files()` (for cited/generated files) |  |
| `llm_node.py` `_process_agent_output` | `node.get_llm_service().get_output_parser()` | Calls get\_llm\_service which hits DB |
| `llm_node.py` `_get_configured_tools` | `node.get_llm_service().attach_built_in_tools(...)` | Calls get\_llm\_service which hits DB |
| `prompt_context.py` PromptTemplateContext | `SourceMaterial.objects.get()`, `Collection.objects.get()`, `Collection.objects.filter()`, `collection.files.values_list()` | `files.values_list` is a downstream FK query |
| `prompt_context.py` ParticipantDataProxy | `session.participant.global_data`, `session.participant.get_schedules_for_experiment()` |  |
| `nodes.py` RenderTemplate | `participant.get_schedules_for_experiment()` |  |
| `nodes.py` CodeNode | `File.create()`, `session.chat.attach_files()` |  |
| `nodes.py` AssistantNode | `OpenAiAssistant.objects.get()` | Deprecated node |
| `nodes.py` field validators | `Collection.objects.in_bulk()` (deserialization) | Out of scope |
| `bots.py` finalization | `ChatMessage.objects.create()`, `SyntheticVoice.objects.filter()`, `ParticipantData.objects.get_or_create()`, `.save()` | Out of scope |
| `history_middleware.py` | Delegates to `HistoryMixin.get_history()` and `store_compression_checkpoint()` — no direct ORM | Indirect |
| `adapters.py` AssistantAdapter | `assistant.llm_provider` (FK), `assistant.llm_provider_model` (FK), `custom_action_operations`, `ToolResources.objects.filter()` | Deprecated node — migrate or drop |

## Solution

Introduce `PipelineRepository` — an ABC that defines all DB operations needed during pipeline execution. Production uses `ORMRepository` (wrapping existing ORM calls); tests use `InMemoryPipelineRepository` with zero DB access backed by `factory_boy` `.build()` instances.

### Key Design Principle: `self.repo` on the Node

The repository is stored as a **private attribute on `BasePipelineNode`** and exposed via a `self.repo` property. This is the single access point — there is no fallback, no optional, no dual-access pattern. The repo **always exists** during pipeline execution.

**Why on the node?**

- Mixins (`HistoryMixin`, `LLMResponseMixin`) are mixed into node classes — they access `self.repo` naturally
- Free functions in `llm_node.py` already receive the node as a parameter — they use `node.repo`
- `PromptTemplateContext` and `ParticipantDataProxy` receive `repo` as a constructor argument (passed from the node)
- No need to thread repo through `NodeContext` — context is for state access, repo is for persistence

**No fallback, no optional (on nodes):**

- `BasePipelineNode._repo` is set during `process()`, before `_process()` is called
- `self.repo` property asserts `_repo is not None` — accessing before `process()` is a programming error
- Nodes, mixins, and helper functions always use `self.repo` / `node.repo` — never fall back to direct ORM
- During Phase 2 migration, each call site is fully switched — no "use repo if available, otherwise ORM" pattern

**Exception: `PromptTemplateContext`:**

- `PromptTemplateContext` is a shared utility also used outside pipeline execution (e.g., `EventBot`, `AssistantAdapter`)
- It accepts `repo=None` — when `None`, uses direct ORM calls (current behavior); when set, uses repo
- Pipeline callers always pass `node.repo`; non-pipeline callers pass nothing

### Import Policy: Top-Level Imports Only

All imports go at the top of the file. Inline/local imports are only used when strictly necessary to avoid circular imports (existing Django convention). The new `repository.py` file imports all model classes at the top level. When migrating call sites in existing files, move any relevant inline imports to the top of the file.

## Injection Flow

The flow from creation to node access:

1. **`bots.py`** creates the repo and puts it in LangGraph config (the only way to pass data through the graph):

```py
# bots.py — PipelineBot._run_pipeline()
repo = ORMRepository()
config = self.trace_service.get_langchain_config(
    configurable={"repo": repo, ...}
)
```

2. **`base.py`** extracts it from config and stores it on the node (before `_process()` is called):

```py
# base.py — PipelineNode.process()
def process(self, state, config, *, incoming_nodes, outgoing_nodes):
    self._config = config
    self._repo = config.get("configurable", {}).get("repo")
    # ... then calls _process()
```

3. **Nodes use `self.repo`** — the only public API:

```py
# example: in a mixin method
service = self.repo.get_llm_service(self.llm_provider_id)

# example: in a free function receiving the node
def _get_search_tool(node: PipelineNode) -> BaseTool | None:
    collections = node.repo.get_collections_for_search(node.collection_index_ids)
```

## RepositoryLookupError

Both `ORMRepository` and `InMemoryPipelineRepository` raise `RepositoryLookupError` when a lookup fails (instead of Django's `DoesNotExist` or Python's `ValueError`). This gives calling code a single exception type to catch, and makes error paths testable without `@pytest.mark.django_db`.

```py
class RepositoryLookupError(Exception):
    """Raised when a repository lookup finds no matching record."""
    pass
```

## CollectionFileInfo NamedTuple

Used as the return type for `get_collection_file_info` and `get_collection_index_summaries`:

```py
from typing import NamedTuple

class CollectionFileInfo(NamedTuple):
    id: int
    summary: str
    content_type: str

class CollectionIndexSummary(NamedTuple):
    id: int
    name: str
    summary: str
```

## PipelineRepository ABC

New file: `apps/pipelines/repository.py`

```py
from abc import ABC, abstractmethod
from typing import NamedTuple

from langchain_core.messages import BaseMessage

from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, SourceMaterial
from apps.files.models import File
from apps.pipelines.models import PipelineChatHistory, PipelineChatMessages
from apps.service_providers.llm_service import LlmService
from apps.service_providers.models import LlmProvider
from apps.documents.models import Collection
from apps.assistants.models import OpenAiAssistant


class RepositoryLookupError(Exception):
    """Raised when a repository lookup finds no matching record."""
    pass


class CollectionFileInfo(NamedTuple):
    id: int
    summary: str
    content_type: str


class CollectionIndexSummary(NamedTuple):
    id: int
    name: str
    summary: str


class PipelineRepository(ABC):
    """ABC for all DB operations during pipeline execution.

    Start monolithic. If this grows beyond ~10 methods in a single domain,
    split into focused ABCs (HistoryRepository, ResourceRepository, etc.).
    """

    # --- Chat history ---

    @abstractmethod
    def get_pipeline_chat_history(
        self, session: ExperimentSession, history_type: str, name: str
    ) -> PipelineChatHistory:
        """Get or create node-specific chat history."""
        ...

    @abstractmethod
    def save_pipeline_chat_message(
        self, history: PipelineChatHistory, human_message: str, ai_message: str, node_id: str
    ) -> PipelineChatMessages:
        """Save a message pair to node-specific history."""
        ...

    @abstractmethod
    def get_session_messages(
        self, chat: Chat, history_mode: str, exclude_message_id: int | None = None
    ) -> list[BaseMessage]:
        """Get session-level chat history as LangChain messages."""
        ...

    @abstractmethod
    def save_compression_checkpoint(
        self, checkpoint_message_id: int, history_type: str, compression_marker: str, history_mode: str
    ) -> None:
        """Update compression markers on history messages.

        For global history: updates either metadata (compression marker sentinel)
        or summary field (real summary text) on ChatMessage.
        For node history: updates fields on PipelineChatMessages.
        """
        ...

    # --- LLM providers & services ---

    @abstractmethod
    def get_llm_provider(self, provider_id: int) -> LlmProvider:
        """Fetch an LLM provider by ID. Raises RepositoryLookupError if not found."""
        ...

    @abstractmethod
    def get_llm_service(self, provider_id: int) -> LlmService:
        """Fetch an LLM provider by ID and return its configured LlmService.

        Wraps both the provider fetch and the service creation, ensuring
        all DB access is captured by the repository.
        Raises RepositoryLookupError if provider not found.
        """
        ...

    # --- Source materials & collections ---

    @abstractmethod
    def get_source_material(self, material_id: int) -> SourceMaterial:
        """Fetch source material by ID. Raises RepositoryLookupError if not found."""
        ...

    @abstractmethod
    def get_collection(self, collection_id: int) -> Collection:
        """Fetch a collection by ID. Raises RepositoryLookupError if not found."""
        ...

    @abstractmethod
    def get_collections_for_search(self, collection_ids: list[int]) -> list[Collection]:
        """Fetch indexed collections by IDs for search tools. Returns materialized list."""
        ...

    @abstractmethod
    def get_collection_index_summaries(self, collection_ids: list[int]) -> list[CollectionIndexSummary]:
        """Fetch collection index id/name/summary tuples for prompt context."""
        ...

    @abstractmethod
    def get_collection_file_info(self, collection_id: int) -> list[CollectionFileInfo]:
        """Fetch file id, summary, and content_type for all files in a collection.

        Used by PromptTemplateContext.get_media_summaries().
        Raises RepositoryLookupError if collection not found.
        """
        ...

    # --- Files ---

    @abstractmethod
    def create_file(
        self, filename: str, content: bytes, team_id: int, content_type: str, purpose: str
    ) -> File:
        """Create a file record."""
        ...

    @abstractmethod
    def attach_files_to_chat(
        self, chat: Chat, attachment_type: str, files: list[File]
    ) -> None:
        """Attach files to a chat. Covers both CodeNode and llm_node _process_files."""
        ...

    # --- Participant ---

    @abstractmethod
    def get_participant_global_data(self, participant) -> dict:
        """Get a participant's global data."""
        ...

    @abstractmethod
    def get_participant_schedules(
        self, participant, experiment_id: int, **kwargs
    ) -> list:
        """Get scheduled messages for a participant."""
        ...

    # --- Assistants (deprecated node support) ---

    @abstractmethod
    def get_assistant(self, assistant_id: int) -> OpenAiAssistant:
        """Fetch an OpenAI assistant by ID. Raises RepositoryLookupError if not found."""
        ...
```

## ORMRepository

In the same file: `apps/pipelines/repository.py`

Each method wraps existing Django ORM calls. Catches `DoesNotExist` and re-raises as `RepositoryLookupError`.

```py
class ORMRepository(PipelineRepository):
    """Production implementation backed by Django ORM.

    # TODO: Per-execution caching optimization
    # ORMRepository is instantiated once per pipeline execution and shared across
    # all nodes via LangGraph config. Multiple nodes often query the same provider
    # or collection (e.g., 3 LLM nodes using the same provider = 3 identical SELECTs).
    # Adding a per-instance dict cache keyed by (method_name, args) would eliminate
    # these duplicate queries. Implementation: check cache before DB query, populate
    # on miss. Cache is naturally scoped to one pipeline execution (one ORMRepository
    # instance) so there are no staleness concerns across executions.
    """

    def get_pipeline_chat_history(self, session, history_type, name):
        history, _ = session.pipeline_chat_history.get_or_create(
            type=history_type, name=name
        )
        return history

    def save_pipeline_chat_message(self, history, human_message, ai_message, node_id):
        return history.messages.create(
            human_message=human_message,
            ai_message=ai_message,
            node_id=node_id,
        )

    def get_session_messages(self, chat, history_mode, exclude_message_id=None):
        return chat.get_langchain_messages_until_marker(
            history_mode, exclude_message_id=exclude_message_id
        )

    def save_compression_checkpoint(self, checkpoint_message_id, history_type, compression_marker, history_mode):
        from apps.chat.conversation import COMPRESSION_MARKER

        if history_type == "global":
            message = ChatMessage.objects.get(id=checkpoint_message_id)
            if compression_marker == COMPRESSION_MARKER:
                message.metadata.update({"compression_marker": history_mode})
                message.save(update_fields=["metadata"])
            else:
                message.summary = compression_marker
                message.save(update_fields=["summary"])
        else:
            updates = {"compression_marker": history_mode}
            if compression_marker != COMPRESSION_MARKER:
                updates["summary"] = compression_marker
            PipelineChatMessages.objects.filter(id=checkpoint_message_id).update(**updates)

    def get_llm_provider(self, provider_id):
        try:
            return LlmProvider.objects.get(id=provider_id)
        except LlmProvider.DoesNotExist:
            raise RepositoryLookupError(f"LLM provider with id {provider_id} not found") from None

    def get_llm_service(self, provider_id):
        provider = self.get_llm_provider(provider_id)
        return provider.get_llm_service()

    def get_source_material(self, material_id):
        try:
            return SourceMaterial.objects.get(id=material_id)
        except SourceMaterial.DoesNotExist:
            raise RepositoryLookupError(f"SourceMaterial with id {material_id} not found") from None

    def get_collection(self, collection_id):
        try:
            return Collection.objects.get(id=collection_id)
        except Collection.DoesNotExist:
            raise RepositoryLookupError(f"Collection with id {collection_id} not found") from None

    def get_collections_for_search(self, collection_ids):
        return list(Collection.objects.filter(id__in=collection_ids, is_index=True))

    def get_collection_index_summaries(self, collection_ids):
        return [
            CollectionIndexSummary(id=row[0], name=row[1], summary=row[2])
            for row in Collection.objects.filter(id__in=collection_ids).values_list("id", "name", "summary")
        ]

    def get_collection_file_info(self, collection_id):
        collection = self.get_collection(collection_id)
        return [
            CollectionFileInfo(id=row[0], summary=row[1], content_type=row[2])
            for row in collection.files.values_list("id", "summary", "content_type")
        ]

    def create_file(self, filename, content, team_id, content_type, purpose):
        return File.create(
            filename=filename, content=content, team_id=team_id,
            content_type=content_type, purpose=purpose,
        )

    def attach_files_to_chat(self, chat, attachment_type, files):
        chat.attach_files(attachment_type=attachment_type, files=files)

    def get_participant_global_data(self, participant):
        return participant.global_data

    def get_participant_schedules(self, participant, experiment_id, **kwargs):
        return participant.get_schedules_for_experiment(experiment_id, **kwargs)

    def get_assistant(self, assistant_id):
        try:
            return OpenAiAssistant.objects.get(id=assistant_id)
        except OpenAiAssistant.DoesNotExist:
            raise RepositoryLookupError(f"Assistant with id {assistant_id} not found") from None
```

## InMemoryPipelineRepository

Uses `factory_boy` `.build()` for realistic model instances without DB access. Pre-load data via constructor or direct attribute assignment.

```py
class InMemoryPipelineRepository(PipelineRepository):
    """Test implementation with no DB access.

    Pre-load data via constructor or direct attribute assignment.
    Uses factory_boy .build() instances for realistic model objects.
    Raises RepositoryLookupError for unconfigured lookups (same as ORMRepository).
    """

    def __init__(self):
        self.providers: dict[int, Any] = {}
        self.llm_services: dict[int, Any] = {}
        self.source_materials: dict[int, Any] = {}
        self.collections: dict[int, Any] = {}
        self.collection_files: dict[int, list[CollectionFileInfo]] = {}
        self.files_created: list[dict] = []
        self.history_messages: list[dict] = []
        self.chat_histories: dict[str, Any] = {}
        self.attached_files: list[dict] = []
        self.assistants: dict[int, Any] = {}
        self.session_messages: list = []
        self.participant_schedules: list = []
        self.participant_global_data: dict = {}

    def get_llm_provider(self, provider_id):
        if provider_id not in self.providers:
            raise RepositoryLookupError(f"LLM provider with id {provider_id} not found")
        return self.providers[provider_id]

    def get_llm_service(self, provider_id):
        if provider_id not in self.llm_services:
            raise RepositoryLookupError(f"LLM service for provider {provider_id} not configured")
        return self.llm_services[provider_id]

    def get_source_material(self, material_id):
        if material_id not in self.source_materials:
            raise RepositoryLookupError(f"SourceMaterial with id {material_id} not found")
        return self.source_materials[material_id]

    def get_collection(self, collection_id):
        if collection_id not in self.collections:
            raise RepositoryLookupError(f"Collection with id {collection_id} not found")
        return self.collections[collection_id]

    def get_collections_for_search(self, collection_ids):
        return [self.collections[cid] for cid in collection_ids if cid in self.collections]

    def get_collection_index_summaries(self, collection_ids):
        results = []
        for cid in collection_ids:
            if cid in self.collections:
                c = self.collections[cid]
                results.append(CollectionIndexSummary(id=c.id, name=c.name, summary=getattr(c, "summary", "")))
        return results

    def get_collection_file_info(self, collection_id):
        if collection_id not in self.collection_files:
            raise RepositoryLookupError(f"Collection with id {collection_id} not found")
        return self.collection_files[collection_id]

    def get_pipeline_chat_history(self, session, history_type, name):
        key = f"{history_type}:{name}"
        if key not in self.chat_histories:
            # Use factory_boy .build() for PipelineChatHistory if available,
            # otherwise use a lightweight stand-in
            from apps.utils.factories.pipelines import PipelineChatHistoryFactory
            self.chat_histories[key] = PipelineChatHistoryFactory.build(
                type=history_type, name=name
            )
        return self.chat_histories[key]

    def save_pipeline_chat_message(self, history, human_message, ai_message, node_id):
        record = {
            "history": history, "human_message": human_message,
            "ai_message": ai_message, "node_id": node_id,
        }
        self.history_messages.append(record)
        # Return a factory-built instance with an explicit ID
        from apps.utils.factories.pipelines import PipelineChatMessagesFactory
        return PipelineChatMessagesFactory.build(
            id=len(self.history_messages),
            human_message=human_message,
            ai_message=ai_message,
            node_id=node_id,
        )

    def get_session_messages(self, chat, history_mode, exclude_message_id=None):
        return list(self.session_messages)

    def save_compression_checkpoint(self, checkpoint_message_id, history_type, compression_marker, history_mode):
        pass  # No-op in tests — verify via assertions on call args if needed

    def create_file(self, filename, content, team_id, content_type, purpose):
        record = {"filename": filename, "content": content, "team_id": team_id,
                   "content_type": content_type, "purpose": purpose}
        self.files_created.append(record)
        from apps.utils.factories.files import FileFactory
        return FileFactory.build(id=len(self.files_created), **record)

    def attach_files_to_chat(self, chat, attachment_type, files):
        self.attached_files.append({"chat": chat, "type": attachment_type, "files": files})

    def get_participant_global_data(self, participant):
        return dict(self.participant_global_data)

    def get_participant_schedules(self, participant, experiment_id, **kwargs):
        return list(self.participant_schedules)

    def get_assistant(self, assistant_id):
        if assistant_id not in self.assistants:
            raise RepositoryLookupError(f"Assistant with id {assistant_id} not found")
        return self.assistants[assistant_id]
```

**Note on `InMemoryPipelineRepository` factories:** The `factory_boy` `.build()` calls above assume factories exist (or will be created) for `PipelineChatHistory`, `PipelineChatMessages`, and `File`. If a factory doesn't exist for a model, create a minimal one with `id = factory.Sequence(lambda n: n + 1)`. For methods where only basic attributes are needed (e.g., `create_file`), the factory approach ensures returned objects have proper IDs and model-like behavior.

## BasePipelineNode Changes

In `apps/pipelines/nodes/base.py`:

```py
class BasePipelineNode(BaseModel, ABC):
    _config: RunnableConfig | None = None
    _repo: PipelineRepository | None = None   # <-- NEW
    _incoming_nodes: list[str] | None = None
    _outgoing_nodes: list[str] | None = None
    # ...

    @property
    def repo(self) -> PipelineRepository:
        """Access the pipeline repository. Always available during _process()."""
        assert self._repo is not None, (
            "PipelineRepository not set. This property is only available during "
            "pipeline execution (after process() has been called)."
        )
        return self._repo
```

The `_repo` is set in `PipelineNode.process()` and `PipelineRouterNode.build_router_function()` — the two entry points from LangGraph — **before** `_process()` or `_process_conditional()` is called:

```py
# PipelineNode.process()
def process(self, state, config, *, incoming_nodes, outgoing_nodes):
    self._config = config
    self._repo = config.get("configurable", {}).get("repo")
    self._incoming_nodes = incoming_nodes
    self._outgoing_nodes = outgoing_nodes
    # ... then _process()

# PipelineRouterNode.build_router_function() -> inner function
def router_function(state, config):
    self._config = config
    self._repo = config.get("configurable", {}).get("repo")
    # ... then _process_conditional()
```

## PromptTemplateContext Changes

In `apps/service_providers/llm_service/prompt_context.py`:

`PromptTemplateContext` accepts an optional `repo` parameter. When `repo` is `None` (default), it uses direct ORM calls (current behavior). When `repo` is provided, it delegates to the repo. This avoids a circular dependency from `prompt_context.py` → `apps/pipelines/repository.py`.

```py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.pipelines.repository import PipelineRepository

class PromptTemplateContext:
    def __init__(
        self,
        session,
        source_material_id: int | None = None,
        collection_id: int | None = None,
        collection_index_ids: list[int] | None = None,
        extra: dict | None = None,
        participant_data: dict | None = None,
        repo: "PipelineRepository | None" = None,  # <-- NEW
    ):
        self.session = session
        self.source_material_id = source_material_id
        self.collection_id = collection_id
        self.collection_index_ids = collection_index_ids or []
        self.extra = extra or {}
        self.context_cache = {}
        self.repo = repo  # <-- NEW
        if participant_data is None:
            participant_data = session.participant_data_from_experiment
        self.participant_data_proxy = ParticipantDataProxy(
            {"participant_data": participant_data}, self.session, repo=repo  # <-- pass repo
        )

    def get_source_material(self):
        if self.source_material_id is None:
            return ""

        if self.repo:
            try:
                return self.repo.get_source_material(self.source_material_id).material
            except RepositoryLookupError:
                return ""
        else:
            # Direct ORM fallback (non-pipeline callers)
            from apps.experiments.models import SourceMaterial
            try:
                return SourceMaterial.objects.get(id=self.source_material_id).material
            except SourceMaterial.DoesNotExist:
                return ""

    def get_media_summaries(self):
        if self.repo:
            try:
                file_info = self.repo.get_collection_file_info(self.collection_id)
                return "\n".join(
                    f"* File (id={fi.id}, content_type={fi.content_type}): {fi.summary}\n"
                    for fi in file_info
                )
            except RepositoryLookupError:
                return ""
        else:
            # Direct ORM fallback
            from apps.documents.models import Collection
            try:
                repo = Collection.objects.get(id=self.collection_id)
                file_info = repo.files.values_list("id", "summary", "content_type")
                return "\n".join(
                    f"* File (id={id}, content_type={content_type}): {summary}\n"
                    for id, summary, content_type in file_info
                )
            except Collection.DoesNotExist:
                return ""

    def get_collection_index_summaries(self):
        if not self.collection_index_ids:
            return ""

        if self.repo:
            summaries = self.repo.get_collection_index_summaries(self.collection_index_ids)
            return "\n".join(
                f"Collection Index (id={s.id}, name={s.name}): {s.summary}" for s in summaries
            )
        else:
            # Direct ORM fallback
            from apps.documents.models import Collection
            collections = Collection.objects.filter(id__in=self.collection_index_ids).values_list("id", "name", "summary")
            return "\n".join(
                f"Collection Index (id={id}, name={name}): {summary}" for id, name, summary in collections
            )

    # get_participant_data and get_current_datetime unchanged — they delegate to ParticipantDataProxy
```

## ParticipantDataProxy Changes

```py
class ParticipantDataProxy:
    def __init__(self, pipeline_state: dict, experiment_session, repo=None):
        self.session = experiment_session
        self.experiment_id = self.session.experiment_id if self.session else None
        self._participant_data = pipeline_state.setdefault("participant_data", {})
        self._scheduled_messages = None
        self.repo = repo  # <-- NEW

    def get(self):
        if self.repo:
            global_data = self.repo.get_participant_global_data(self.session.participant)
        else:
            global_data = self.session.participant.global_data
        return global_data | self._participant_data

    def get_schedules(self):
        if self._scheduled_messages is None:
            if self.repo:
                self._scheduled_messages = self.repo.get_participant_schedules(
                    self.session.participant, self.experiment_id,
                    as_dict=True, as_timezone=self.get_timezone()
                )
            else:
                self._scheduled_messages = self.session.participant.get_schedules_for_experiment(
                    self.experiment_id, as_dict=True, as_timezone=self.get_timezone()
                )
        return self._scheduled_messages
```

## Migration Strategy (3 phases)

### Phase 1: Infrastructure (no behavior change) - DONE!

**Files:** `repository.py` (new), `base.py` (modify), `bots.py` (modify), test files (new)

1. Create `apps/pipelines/repository.py` with:
   - `RepositoryLookupError` exception
   - `CollectionFileInfo` and `CollectionIndexSummary` NamedTuples
   - `PipelineRepository` ABC with all abstract methods
   - `ORMRepository` — each method wraps an existing ORM call, catches `DoesNotExist` → `RepositoryLookupError`
   - `InMemoryPipelineRepository` with `factory_boy` `.build()` backed stores
   - All model imports at the top of the file
2. Add `_repo` private attribute and `repo` property to `BasePipelineNode` — modify `base.py`
3. Extract repo from config and set `self._repo` in both `PipelineNode.process()` and `PipelineRouterNode.build_router_function()` — modify `base.py`
4. Inject `ORMRepository` via config in `PipelineBot._run_pipeline()` — modify `bots.py`
5. Verify/add `id = factory.Sequence(lambda n: n + 1)` to factories used by `InMemoryPipelineRepository` (check `apps/utils/factories/`)
6. Write **shared parametrized test suite** for both `ORMRepository` and `InMemoryPipelineRepository`:
   - Test happy paths for each method
   - Test `RepositoryLookupError` for each lookup method
   - `ORMRepository` tests use `@pytest.mark.django_db` \+ factory\_boy `.create()`
   - `InMemoryPipelineRepository` tests run without DB
   - File: `apps/pipelines/tests/test_repository.py`
7. Write **injection wiring integration test**:
   - Create a minimal pipeline, invoke via `PipelineBot`, assert `self.repo` is set and is `ORMRepository`
   - File: `apps/pipelines/tests/test_repository.py` (same file)

**After Phase 1:** All existing code works unchanged. `self.repo` is available on every node but unused. Existing ORM calls remain in place.

### Phase 2: Migrate DB access to repository

**Phase 2 step 0:** Update `PipelineTestBot` to inject `ORMRepository` via config (prevents breakage during subsequent steps). `PipelineTestBot` is used in `apps/pipelines/models.py:239` for pipeline validation — this is production code, not just tests. — **DONE**

Migrate call sites one at a time. Each migration is independent and follows the same pattern: replace the direct ORM call with `self.repo.method(...)` (or `node.repo.method(...)` for free functions). **No fallback** — once migrated, the old ORM call is deleted, not wrapped in a conditional.

**Each step includes updating corresponding tests** — replace ORM mocks with `InMemoryPipelineRepository` injection. Each step must leave tests green.

#### 2a. HistoryMixin (`apps/pipelines/nodes/mixins.py`) — DONE

- Replace `session.pipeline_chat_history.get_or_create(...)` → `self.repo.get_pipeline_chat_history(...)`
- Replace `history.messages.create(...)` → `self.repo.save_pipeline_chat_message(...)`
- Replace `session.chat.get_langchain_messages_until_marker(...)` → `self.repo.get_session_messages(...)`
- Replace compression checkpoint updates → `self.repo.save_compression_checkpoint(...)` (pass both `compression_marker` and `history_mode`)
- Since `HistoryMixin` is mixed into node classes, `self.repo` is available directly.
- **Update `TestHistoryMixin` in `test_nodes.py`**: replace `patch("apps.pipelines.nodes.mixins.ChatMessage")` and `patch("apps.pipelines.nodes.mixins.PipelineChatMessages")` with `InMemoryPipelineRepository` injection.

#### 2b. LLMResponseMixin (`apps/pipelines/nodes/mixins.py`) — DONE

- Replace `LlmProvider.objects.get(...)` \+ `provider.get_llm_service()` in `get_llm_service()` → `self.repo.get_llm_service(...)`
- **Note:** The cached `get_llm_provider()` / `get_llm_provider_model()` module-level helpers used in **validators** remain unchanged — validators run at deserialization time before `process()`, so they cannot use `self.repo`. These are explicitly out of scope.
- **Update relevant tests** that mock `LlmProvider.objects.get`.

#### 2c. `llm_node.py` `_get_search_tool` (`apps/pipelines/nodes/llm_node.py`) — DONE

- Replace `Collection.objects.filter(...)` → `node.repo.get_collections_for_search(...)`
- `_get_search_tool` already receives the node as its first argument, so `node.repo` is available.
- **Update corresponding tests.**

#### 2d. `llm_node.py` `_process_files` and `_process_agent_output` (`apps/pipelines/nodes/llm_node.py`) — DONE

- Replace `session.chat.attach_files(...)` → `node.repo.attach_files_to_chat(...)`
- `_process_agent_output` and `_get_configured_tools` use `node.get_llm_service()` which delegates to `self.repo.get_llm_service(...)` via `LLMResponseMixin`
- `_process_files` signature changed to receive `node` as first argument (threaded from `_process_agent_output`)
- **Updated corresponding tests** — added `ORMRepository` injection to test configs.

#### 2e. PromptTemplateContext (`apps/service_providers/llm_service/prompt_context.py`) — DONE

- Add `repo` parameter to `PromptTemplateContext.__init__()` (optional, defaults to `ORMRepository()`)
- `PromptTemplateContext` always uses `self.repo` — no ORM fallback branches
- Replace `SourceMaterial.objects.get(...)` → `self.repo.get_source_material(...)`
- Replace `Collection.objects.get/filter(...)` → `self.repo.get_collection_file_info(...)` / `self.repo.get_collection_index_summaries(...)`
- Callers (`_get_prompt_context` in `llm_node.py`, `RouterNode._process_conditional` in `nodes.py`) pass `node.repo` when constructing `PromptTemplateContext`.
- Non-pipeline callers (`EventBot`, `AssistantAdapter`) pass nothing — `repo=None` defaults to `ORMRepository()`.
- Broke circular import chain by moving `OpenAiAssistant` import in `repository.py` to `TYPE_CHECKING` (only used in abstract return type annotation + inlined in `ORMRepository.get_assistant()`).
- **Updated `test_prompt_context.py` tests** — mock target changed to `PipelineParticipantDataProxy`.

#### 2f. ParticipantDataProxy (`apps/service_providers/llm_service/prompt_context.py`) — DONE

- Created `PipelineParticipantDataProxy` subclass that accepts `repo` and overrides `get()` and `get_schedules()` to use the repo.
- Base `ParticipantDataProxy` remains unchanged (no repo param, direct ORM access) — used by `chat/agent/tools.py` tool actions.
- `PromptTemplateContext` uses `PipelineParticipantDataProxy` with its repo.
- `CodeNode._get_custom_functions()` uses `PipelineParticipantDataProxy` with `self.repo`.
- **Updated corresponding tests.**

#### 2g. CodeNode file attachment (`apps/pipelines/nodes/nodes.py`) — DONE

- Replace `File.create(...)` → `self.repo.create_file(...)`
- Replace `session.chat.attach_files(...)` → `self.repo.attach_files_to_chat(...)`
- `self.repo` is available directly since CodeNode extends `BasePipelineNode`.
- **Update `test_code_node.py` tests.**

#### 2h. RenderTemplate schedules (`apps/pipelines/nodes/nodes.py`) — DONE

- Replace `participant.get_schedules_for_experiment(...)` → `self.repo.get_participant_schedules(...)`
- `self.repo` is available directly since RenderTemplate extends `BasePipelineNode`.
- **Update `test_template_node.py` tests.**

#### 2i. AssistantNode (deprecated) (`apps/pipelines/nodes/nodes.py`) — DONE

- Replace `OpenAiAssistant.objects.get(...)` → `self.repo.get_assistant(...)`
- `self.repo` is available directly since AssistantNode extends `BasePipelineNode`.
- **Note:** `AssistantAdapter` has additional FK traversals (`assistant.llm_provider`, `assistant.llm_provider_model`, `custom_action_operations`, `ToolResources`). These are in a deprecated code path. Document as "migrate when time permits or drop when AssistantNode is removed."
- **Update corresponding tests.**

#### 2j. Eliminate session FK traversals — DONE

- Changed `get_session_messages()` and `attach_files_to_chat()` ABC signatures from `chat: Chat` → `session: ExperimentSession` so callers no longer traverse `session.chat`
- Added `get_session_team(session)` and `get_session_participant(session)` to the ABC to eliminate `session.team` and `session.participant` FK traversals
- Updated all callers in `mixins.py`, `llm_node.py`, and `nodes.py`
- `ORMRepository` implementations do the FK traversal internally; `InMemoryPipelineRepository` uses `getattr()` on pre-built session objects
- Removed unused `Chat` import from `repository.py`
- **Updated all pipeline tests (289 passing).**

### Phase 3: Test migration and cleanup — DONE

1. **Test migration** — Converted `test_get_participant_schedules_empty` and `test_get_and_set_session_state` from `@pytest.mark.django_db` to `InMemoryPipelineRepository` with `ExperimentSessionFactory.build()`. 13+ tests now run without DB access. Multi-node integration tests (`test_participant_data_across_multiple_nodes`, etc.) remain as DB tests because `create_runnable` → `Pipeline.update_nodes_from_data()` requires the ORM.
2. **Documentation** — Created `docs/agents/pipeline_repository.md` covering usage, adding new operations, error handling, and test patterns. Added reference in `AGENTS.md`.
3. **ABC split evaluation** — The ABC has 18 methods across 7 domains, with no domain exceeding 5 methods (the threshold was ~10). **Decision: keep monolithic.** The current grouping is well-scoped and splitting would add complexity without benefit. Revisit if a single domain grows beyond 10 methods.

## Critical Files

| File | Action |
| :---- | :---- |
| `apps/pipelines/repository.py` | CREATE — ABC \+ ORMRepository \+ InMemory \+ RepositoryLookupError \+ NamedTuples |
| `apps/pipelines/nodes/base.py` | MODIFY — add `_repo`, `repo` property, set in `process()` and `build_router_function()` |
| `apps/chat/bots.py` | MODIFY — inject `ORMRepository` via config in `PipelineBot` and `PipelineTestBot` |
| `apps/pipelines/nodes/mixins.py` | MODIFY — HistoryMixin and LLMResponseMixin use `self.repo` |
| `apps/pipelines/nodes/llm_node.py` | MODIFY — use `node.repo` for collections, file attachment, LLM service; pass repo to PromptTemplateContext |
| `apps/pipelines/nodes/nodes.py` | MODIFY — CodeNode, AssistantNode, RenderTemplate use `self.repo` |
| `apps/service_providers/llm_service/prompt_context.py` | MODIFY — PromptTemplateContext and ParticipantDataProxy accept optional `repo` |
| `apps/pipelines/tests/test_repository.py` | CREATE — shared parametrized tests for both implementations \+ injection wiring test |
| `apps/pipelines/tests/test_nodes.py` | MODIFY — replace ORM mocks with InMemoryPipelineRepository |
| `apps/pipelines/tests/test_code_node.py` | MODIFY — replace ORM mocks with InMemoryPipelineRepository |
| `apps/pipelines/tests/test_template_node.py` | MODIFY — replace ORM mocks with InMemoryPipelineRepository |
| `apps/service_providers/tests/test_prompt_context.py` | MODIFY — add tests for repo and fallback paths |
| `apps/utils/factories/` | MODIFY — verify `id = factory.Sequence(...)` on relevant factories |

## Key Design Decisions

1. **`self.repo` on `BasePipelineNode` is the API** — not on NodeContext. Repo is for persistence, context is for state access. Mixins and node methods access `self.repo` directly. Free functions receive the node and use `node.repo`.

2. **Repo is always present on nodes, never optional.** `_repo` defaults to `None` only because of how Pydantic private attrs work. The `repo` property asserts it is set. If accessed before `process()`, it's a programming error. There is no "use repo if available, otherwise fall back to ORM" pattern on nodes.

3. **`PromptTemplateContext` is the exception.** It accepts optional `repo=None`. When `None`, uses direct ORM (for non-pipeline callers like `EventBot`). When set, uses repo. This is a contained exception — `PromptTemplateContext` is a shared utility that predates the repository pattern.

4. **`PipelineRepository` is an ABC, not a Protocol.** Both `ORMRepository` and `InMemoryPipelineRepository` explicitly subclass it. This gives nominal typing, `isinstance()` support, and explicit "implements" declarations.

5. **`RepositoryLookupError`** is the single exception type for failed lookups. `ORMRepository` catches `DoesNotExist` and re-raises. `InMemoryPipelineRepository` raises it directly for missing data. Calling code catches `RepositoryLookupError`.

6. **Return types are concrete** — `list[Collection]` not `QuerySet`. `NamedTuple`s (`CollectionFileInfo`, `CollectionIndexSummary`) for tuple returns. The protocol is the contract; types must be satisfiable by both implementations.

7. **`InMemoryPipelineRepository` uses `factory_boy` `.build()`** for realistic model instances without DB access. Factories must have `id = factory.Sequence(...)` so `.build()` produces instances with proper IDs.

8. **Repo wraps 100% of DB access during pipeline execution.** This includes downstream FK queries on returned model instances. `get_llm_service()` wraps both provider fetch \+ service creation. `get_collection_file_info()` wraps collection fetch \+ files FK traversal. `get_participant_global_data()` wraps `participant.global_data` access.

9. **No inline imports.** All model imports in `repository.py` are at the top of the file. `prompt_context.py` uses `if TYPE_CHECKING:` for the `PipelineRepository` type hint to avoid circular imports.

10. **Each Phase 2 step includes test updates.** No step leaves tests in a broken state. ORM mocks are replaced with `InMemoryPipelineRepository` injection as each call site is migrated.

11. **Start monolithic** — one ABC, one implementation class. Split only if it grows beyond \~10 methods in a single domain.

12. **Validators are out of scope** — module-level cached helpers (`get_llm_provider_model`, `get_llm_provider`) used at deserialization time remain unchanged. They run before `process()`, before the repo is set.

13. **`AssistantAdapter` FK traversals (deprecated node)** — document as "migrate when time permits or drop when AssistantNode is removed." These include `assistant.llm_provider`, `assistant.llm_provider_model`, `custom_action_operations.exists()`, `ToolResources.objects.filter()`.

## Verification

- Existing tests pass: `uv run pytest apps/pipelines/tests/ -v` at every step
- New repository tests: shared parametrized suite for both `ORMRepository` and `InMemoryPipelineRepository`
- Injection wiring test: end-to-end test verifying `PipelineBot` → config → `node.repo`
- DB isolation test: At least one node test using `InMemoryPipelineRepository` that does NOT use `@pytest.mark.django_db`
- Lint/type check: `uv run ruff check apps/pipelines/ apps/service_providers/llm_service/prompt_context.py` and `uv run ruff format apps/pipelines/ apps/service_providers/llm_service/prompt_context.py`

## Out of Scope

- Field validator DB access (`Collection.objects.in_bulk` at deserialization time) — happens before `process()`, needs a different solution
- DB access in `bots.py` finalization (`_save_outputs`, `_save_message_to_history`, `get_synthetic_voice`) — runs after pipeline execution, outside the node boundary. Could be a future Refactor 3\.
- `helpers.py` `temporary_session` — test infrastructure, not pipeline execution
- Module-level `lru_cache` functions (`get_llm_provider_model`, `get_llm_provider`) used in validators — deserialization time, not `_process` time
- Splitting the ABC into focused sub-ABCs — plan for it, but start monolithic
- `AssistantAdapter` FK traversals beyond `get_assistant()` — deprecated node, document and defer
