from __future__ import annotations

from abc import ABC, abstractmethod
from io import BytesIO
from typing import TYPE_CHECKING, Any, NamedTuple

from langchain_core.messages import BaseMessage

from apps.chat.models import ChatMessage
from apps.documents.models import Collection
from apps.experiments.models import ExperimentSession, SourceMaterial
from apps.files.models import File
from apps.pipelines.models import PipelineChatHistory, PipelineChatMessages
from apps.service_providers.llm_service import LlmService
from apps.service_providers.models import LlmProvider, LlmProviderModel

if TYPE_CHECKING:
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
        self, session: ExperimentSession, history_mode: str, exclude_message_id: int | None = None
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

    @abstractmethod
    def get_llm_provider_model(self, model_id: int) -> LlmProviderModel:
        """Fetch an LLM provider model by ID. Raises RepositoryLookupError if not found."""
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
        self, filename: str, file_obj: BytesIO, team_id: int, content_type: str | None, purpose: str
    ) -> File:
        """Create a file record."""
        ...

    @abstractmethod
    def attach_files_to_chat(
        self, session: ExperimentSession, attachment_type: str, files: list[File] | set[File]
    ) -> None:
        """Attach files to a chat via the session. Covers both CodeNode and llm_node _process_files."""
        ...

    # --- Participant ---

    @abstractmethod
    def get_participant_global_data(self, participant) -> dict:
        """Get a participant's global data."""
        ...

    @abstractmethod
    def get_participant_schedules(self, participant, experiment_id, **kwargs) -> list:
        """Get scheduled messages for a participant."""
        ...

    # --- Session accessors ---

    @abstractmethod
    def get_session_team(self, session: ExperimentSession):
        """Get the team for a session (avoids FK traversal on session.team)."""
        ...

    @abstractmethod
    def get_session_participant(self, session: ExperimentSession):
        """Get the participant for a session (avoids FK traversal on session.participant)."""
        ...

    # --- Assistants (deprecated node support) ---

    @abstractmethod
    def get_assistant(self, assistant_id: int) -> OpenAiAssistant:
        """Fetch an OpenAI assistant by ID. Raises RepositoryLookupError if not found."""
        ...


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
        history, _ = session.pipeline_chat_history.get_or_create(type=history_type, name=name)
        return history

    def save_pipeline_chat_message(self, history, human_message, ai_message, node_id):
        return history.messages.create(
            human_message=human_message,
            ai_message=ai_message,
            node_id=node_id,
        )

    def get_session_messages(self, session, history_mode, exclude_message_id=None):
        return session.chat.get_langchain_messages_until_marker(history_mode, exclude_message_id=exclude_message_id)

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

    def get_llm_provider_model(self, model_id):
        try:
            return LlmProviderModel.objects.get(id=model_id)
        except LlmProviderModel.DoesNotExist:
            raise RepositoryLookupError(f"LLM provider model with id {model_id} not found") from None

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

    def create_file(self, filename, file_obj, team_id, content_type, purpose):
        return File.create(
            filename=filename,
            file_obj=file_obj,
            team_id=team_id,
            content_type=content_type,
            purpose=purpose,
        )

    def attach_files_to_chat(self, session, attachment_type, files):
        session.chat.attach_files(attachment_type=attachment_type, files=files)

    def get_participant_global_data(self, participant):
        return participant.global_data

    def get_participant_schedules(self, participant, experiment_id, **kwargs):
        return participant.get_schedules_for_experiment(experiment_id, **kwargs)

    def get_assistant(self, assistant_id):
        from apps.assistants.models import OpenAiAssistant

        try:
            return OpenAiAssistant.objects.get(id=assistant_id)
        except OpenAiAssistant.DoesNotExist:
            raise RepositoryLookupError(f"Assistant with id {assistant_id} not found") from None

    def get_session_team(self, session):
        return session.team

    def get_session_participant(self, session):
        return session.participant


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
        self.session_messages: list[BaseMessage] = []
        self.participant_schedules: list = []
        self.participant_global_data: dict = {}
        self.compression_checkpoints: list[dict] = []
        self.provider_models: dict[int, Any] = {}

    def get_llm_provider(self, provider_id):
        if provider_id not in self.providers:
            raise RepositoryLookupError(f"LLM provider with id {provider_id} not found")
        return self.providers[provider_id]

    def get_llm_service(self, provider_id):
        if provider_id not in self.llm_services:
            raise RepositoryLookupError(f"LLM service for provider {provider_id} not configured")
        return self.llm_services[provider_id]

    def get_llm_provider_model(self, model_id):
        if model_id not in self.provider_models:
            raise RepositoryLookupError(f"LLM provider model with id {model_id} not found")
        return self.provider_models[model_id]

    def get_source_material(self, material_id):
        if material_id not in self.source_materials:
            raise RepositoryLookupError(f"SourceMaterial with id {material_id} not found")
        return self.source_materials[material_id]

    def get_collection(self, collection_id):
        if collection_id not in self.collections:
            raise RepositoryLookupError(f"Collection with id {collection_id} not found")
        return self.collections[collection_id]

    def get_collections_for_search(self, collection_ids):
        return [
            collection
            for cid in collection_ids
            if (collection := self.collections.get(cid)) and getattr(collection, "is_index", False)
        ]

    def get_collection_index_summaries(self, collection_ids):
        results = []
        for cid in collection_ids:
            if cid in self.collections:
                c = self.collections[cid]
                results.append(CollectionIndexSummary(id=c.id, name=c.name, summary=getattr(c, "summary", "")))
        return results

    def get_collection_file_info(self, collection_id):
        if collection_id not in self.collections:
            raise RepositoryLookupError(f"Collection with id {collection_id} not found")
        return list(self.collection_files.get(collection_id, []))

    def get_pipeline_chat_history(self, session, history_type, name):
        key = f"{history_type}:{name}"
        if key not in self.chat_histories:
            from apps.utils.factories.pipelines import PipelineChatHistoryFactory

            self.chat_histories[key] = PipelineChatHistoryFactory.build(type=history_type, name=name)
        return self.chat_histories[key]

    def save_pipeline_chat_message(self, history, human_message, ai_message, node_id):
        record = {
            "history": history,
            "human_message": human_message,
            "ai_message": ai_message,
            "node_id": node_id,
        }
        self.history_messages.append(record)
        from apps.utils.factories.pipelines import PipelineChatMessagesFactory

        return PipelineChatMessagesFactory.build(
            id=len(self.history_messages),
            human_message=human_message,
            ai_message=ai_message,
            node_id=node_id,
        )

    def get_session_messages(self, session, history_mode, exclude_message_id=None):
        return list(self.session_messages)

    def save_compression_checkpoint(self, checkpoint_message_id, history_type, compression_marker, history_mode):
        self.compression_checkpoints.append(
            {
                "checkpoint_message_id": checkpoint_message_id,
                "history_type": history_type,
                "compression_marker": compression_marker,
                "history_mode": history_mode,
            }
        )

    def create_file(self, filename, file_obj, team_id, content_type, purpose):
        record = {
            "filename": filename,
            "team_id": team_id,
            "content_type": content_type,
            "purpose": purpose,
        }
        self.files_created.append(record)
        from apps.utils.factories.files import FileFactory

        return FileFactory.build(id=len(self.files_created), name=filename, content_type=content_type or "")

    def attach_files_to_chat(self, session, attachment_type, files):
        self.attached_files.append({"session": session, "type": attachment_type, "files": files})

    def get_participant_global_data(self, participant):
        return dict(self.participant_global_data)

    def get_participant_schedules(self, participant, experiment_id, **kwargs):
        return list(self.participant_schedules)

    def get_session_team(self, session):
        return getattr(session, "team", None)

    def get_session_participant(self, session):
        return getattr(session, "participant", None)

    def get_assistant(self, assistant_id):
        if assistant_id not in self.assistants:
            raise RepositoryLookupError(f"Assistant with id {assistant_id} not found")
        return self.assistants[assistant_id]
