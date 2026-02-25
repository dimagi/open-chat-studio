from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage

if TYPE_CHECKING:
    from apps.assistants.models import OpenAiAssistant
    from apps.chat.models import Chat
    from apps.documents.models import Collection
    from apps.experiments.models import ExperimentSession, SourceMaterial
    from apps.files.models import File
    from apps.pipelines.models import PipelineChatHistory, PipelineChatMessages
    from apps.service_providers.models import LlmProvider


@runtime_checkable
class PipelineRepository(Protocol):
    """Defines all DB operations needed during pipeline execution.

    Implementations must satisfy every method listed here. Nodes will
    eventually call these through ``context.repo`` instead of hitting the
    ORM directly.
    """

    # -- Chat history ---------------------------------------------------------

    def get_pipeline_chat_history(
        self,
        session: ExperimentSession,
        history_type: str,
        history_name: str,
    ) -> PipelineChatHistory | None:
        """Return an existing pipeline chat history record, or None."""
        ...

    def get_or_create_pipeline_chat_history(
        self,
        session: ExperimentSession,
        history_type: str,
        history_name: str,
    ) -> tuple[PipelineChatHistory, bool]:
        """Return an existing history record or create a new one.

        Returns a ``(history, created)`` tuple.
        """
        ...

    def save_pipeline_chat_message(
        self,
        history: PipelineChatHistory,
        node_id: str,
        human_message: str,
        ai_message: str,
    ) -> PipelineChatMessages:
        """Persist a new human/AI message pair in the pipeline chat history."""
        ...

    def get_session_messages_until_marker(
        self,
        chat: Chat,
        marker: str,
        exclude_message_id: int | None = None,
    ) -> list[BaseMessage]:
        """Fetch session-level (global) messages up to a compression marker."""
        ...

    def save_compression_checkpoint_global(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        """Save a compression checkpoint on a global ChatMessage."""
        ...

    def save_compression_checkpoint_pipeline(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        """Save a compression checkpoint on a PipelineChatMessages record."""
        ...

    # -- LLM providers --------------------------------------------------------

    def get_llm_provider(self, llm_provider_id: int) -> LlmProvider | None:
        """Return an LlmProvider by id, or None if not found."""
        ...

    # -- Source materials & collections ----------------------------------------

    def get_source_material(self, source_material_id: int) -> SourceMaterial | None:
        """Return a SourceMaterial by id, or None if not found."""
        ...

    def get_collection(self, collection_id: int) -> Collection | None:
        """Return a Collection by id, or None if not found."""
        ...

    def get_collections_for_search(self, collection_ids: list[int]) -> list[Collection]:
        """Return Collection objects matching the given ids (index collections)."""
        ...

    def get_collection_index_summaries(self, collection_index_ids: list[int]) -> str:
        """Return a formatted string of collection index summaries."""
        ...

    # -- Files -----------------------------------------------------------------

    def create_file(
        self,
        filename: str,
        file_obj: BytesIO,
        team_id: int,
        content_type: str | None = None,
        purpose: str | None = None,
    ) -> File:
        """Create and persist a file record."""
        ...

    def attach_files_to_chat(
        self,
        chat: Chat,
        attachment_type: str,
        files: list[File],
    ) -> None:
        """Attach files to a chat session."""
        ...

    # -- Participant -----------------------------------------------------------

    def get_participant_schedules(
        self,
        participant: Any,
        experiment_id: int,
        as_dict: bool = True,
        as_timezone: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict] | list[str]:
        """Return scheduled messages for a participant in an experiment."""
        ...

    # -- Assistants ------------------------------------------------------------

    def get_assistant(self, assistant_id: int) -> OpenAiAssistant | None:
        """Return an OpenAiAssistant by id, or None if not found."""
        ...


class DjangoPipelineRepository:
    """Production implementation backed by the Django ORM.

    Each method wraps ORM calls that are currently scattered across pipeline
    nodes, mixins, and prompt context helpers.
    """

    # -- Chat history ---------------------------------------------------------

    def get_pipeline_chat_history(
        self,
        session: ExperimentSession,
        history_type: str,
        history_name: str,
    ) -> PipelineChatHistory | None:
        from apps.pipelines.models import PipelineChatHistory

        try:
            return session.pipeline_chat_history.get(type=history_type, name=history_name)
        except PipelineChatHistory.DoesNotExist:
            return None

    def get_or_create_pipeline_chat_history(
        self,
        session: ExperimentSession,
        history_type: str,
        history_name: str,
    ) -> tuple[PipelineChatHistory, bool]:
        return session.pipeline_chat_history.get_or_create(type=history_type, name=history_name)

    def save_pipeline_chat_message(
        self,
        history: PipelineChatHistory,
        node_id: str,
        human_message: str,
        ai_message: str,
    ) -> PipelineChatMessages:
        return history.messages.create(
            human_message=human_message,
            ai_message=ai_message,
            node_id=node_id,
        )

    def get_session_messages_until_marker(
        self,
        chat: Chat,
        marker: str,
        exclude_message_id: int | None = None,
    ) -> list[BaseMessage]:
        return chat.get_langchain_messages_until_marker(marker=marker, exclude_message_id=exclude_message_id)

    def save_compression_checkpoint_global(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        from apps.chat.conversation import COMPRESSION_MARKER
        from apps.chat.models import ChatMessage

        message = ChatMessage.objects.get(id=message_id)
        if compression_marker == COMPRESSION_MARKER:
            message.metadata.update({"compression_marker": history_mode})
            message.save(update_fields=["metadata"])
        else:
            message.summary = compression_marker
            message.save(update_fields=["summary"])

    def save_compression_checkpoint_pipeline(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        from apps.chat.conversation import COMPRESSION_MARKER
        from apps.pipelines.models import PipelineChatMessages

        updates: dict[str, Any] = {"compression_marker": history_mode}
        if compression_marker != COMPRESSION_MARKER:
            updates["summary"] = compression_marker
        PipelineChatMessages.objects.filter(id=message_id).update(**updates)

    # -- LLM providers --------------------------------------------------------

    def get_llm_provider(self, llm_provider_id: int) -> LlmProvider | None:
        from apps.service_providers.models import LlmProvider

        try:
            return LlmProvider.objects.get(id=llm_provider_id)
        except LlmProvider.DoesNotExist:
            return None

    # -- Source materials & collections ----------------------------------------

    def get_source_material(self, source_material_id: int) -> SourceMaterial | None:
        from apps.experiments.models import SourceMaterial

        try:
            return SourceMaterial.objects.get(id=source_material_id)
        except SourceMaterial.DoesNotExist:
            return None

    def get_collection(self, collection_id: int) -> Collection | None:
        from apps.documents.models import Collection

        try:
            return Collection.objects.get(id=collection_id)
        except Collection.DoesNotExist:
            return None

    def get_collections_for_search(self, collection_ids: list[int]) -> list[Collection]:
        from apps.documents.models import Collection

        return list(Collection.objects.filter(id__in=collection_ids, is_index=True))

    def get_collection_index_summaries(self, collection_index_ids: list[int]) -> str:
        from apps.documents.models import Collection

        if not collection_index_ids:
            return ""

        collections = Collection.objects.filter(id__in=collection_index_ids).values_list("id", "name", "summary")
        return "\n".join([f"Collection Index (id={id}, name={name}): {summary}" for id, name, summary in collections])

    # -- Files -----------------------------------------------------------------

    def create_file(
        self,
        filename: str,
        file_obj: BytesIO,
        team_id: int,
        content_type: str | None = None,
        purpose: str | None = None,
    ) -> File:
        from apps.files.models import File, FilePurpose

        return File.create(
            filename=filename,
            file_obj=file_obj,
            team_id=team_id,
            content_type=content_type,
            purpose=FilePurpose(purpose) if purpose else FilePurpose.MESSAGE_MEDIA,
        )

    def attach_files_to_chat(
        self,
        chat: Chat,
        attachment_type: str,
        files: list[File],
    ) -> None:
        chat.attach_files(attachment_type=attachment_type, files=files)

    # -- Participant -----------------------------------------------------------

    def get_participant_schedules(
        self,
        participant: Any,
        experiment_id: int,
        as_dict: bool = True,
        as_timezone: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict] | list[str]:
        return participant.get_schedules_for_experiment(
            experiment_id,
            as_dict=as_dict,
            as_timezone=as_timezone,
            include_inactive=include_inactive,
        )

    # -- Assistants ------------------------------------------------------------

    def get_assistant(self, assistant_id: int) -> OpenAiAssistant | None:
        from apps.assistants.models import OpenAiAssistant

        try:
            return OpenAiAssistant.objects.get(id=assistant_id)
        except OpenAiAssistant.DoesNotExist:
            return None


class InMemoryPipelineRepository:
    """Test-only implementation with dict-based stores and no DB access.

    Pre-load data via the constructor or setters. Returns configured data
    or ``None`` for unconfigured lookups. Records calls for test assertions.
    """

    def __init__(
        self,
        *,
        llm_providers: dict[int, Any] | None = None,
        source_materials: dict[int, Any] | None = None,
        collections: dict[int, Any] | None = None,
        assistants: dict[int, Any] | None = None,
        chat_histories: dict[tuple[int, str, str], Any] | None = None,
    ):
        self._llm_providers = llm_providers or {}
        self._source_materials = source_materials or {}
        self._collections = collections or {}
        self._assistants = assistants or {}
        self._chat_histories = chat_histories or {}

        # Tracking lists for assertions
        self.files_created: list[dict] = []
        self.attached_files: list[dict] = []
        self.saved_messages: list[dict] = []
        self.compression_checkpoints: list[dict] = []
        self.schedule_lookups: list[dict] = []

    # -- Chat history ---------------------------------------------------------

    def get_pipeline_chat_history(
        self,
        session: Any,
        history_type: str,
        history_name: str,
    ) -> Any | None:
        session_id = session.id if hasattr(session, "id") else session
        return self._chat_histories.get((session_id, history_type, history_name))

    def get_or_create_pipeline_chat_history(
        self,
        session: Any,
        history_type: str,
        history_name: str,
    ) -> tuple[Any, bool]:
        session_id = session.id if hasattr(session, "id") else session
        key = (session_id, history_type, history_name)
        if key in self._chat_histories:
            return self._chat_histories[key], False
        history = {"session_id": session_id, "type": history_type, "name": history_name, "messages": []}
        self._chat_histories[key] = history
        return history, True

    def save_pipeline_chat_message(
        self,
        history: Any,
        node_id: str,
        human_message: str,
        ai_message: str,
    ) -> dict:
        message = {
            "history": history,
            "node_id": node_id,
            "human_message": human_message,
            "ai_message": ai_message,
        }
        self.saved_messages.append(message)
        return message

    def get_session_messages_until_marker(
        self,
        chat: Any,
        marker: str,
        exclude_message_id: int | None = None,
    ) -> list[BaseMessage]:
        return []

    def save_compression_checkpoint_global(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        self.compression_checkpoints.append(
            {
                "type": "global",
                "message_id": message_id,
                "compression_marker": compression_marker,
                "history_mode": history_mode,
            }
        )

    def save_compression_checkpoint_pipeline(
        self,
        message_id: int,
        compression_marker: str,
        history_mode: str,
    ) -> None:
        self.compression_checkpoints.append(
            {
                "type": "pipeline",
                "message_id": message_id,
                "compression_marker": compression_marker,
                "history_mode": history_mode,
            }
        )

    # -- LLM providers --------------------------------------------------------

    def get_llm_provider(self, llm_provider_id: int) -> Any | None:
        return self._llm_providers.get(llm_provider_id)

    # -- Source materials & collections ----------------------------------------

    def get_source_material(self, source_material_id: int) -> Any | None:
        return self._source_materials.get(source_material_id)

    def get_collection(self, collection_id: int) -> Any | None:
        return self._collections.get(collection_id)

    def get_collections_for_search(self, collection_ids: list[int]) -> list[Any]:
        results = []
        for cid in collection_ids:
            coll = self._collections.get(cid)
            if coll is None:
                continue
            is_index = getattr(coll, "is_index", None)
            if is_index is None and isinstance(coll, dict):
                is_index = coll.get("is_index", True)
            if is_index is not False:
                results.append(coll)
        return results

    def get_collection_index_summaries(self, collection_index_ids: list[int]) -> str:
        if not collection_index_ids:
            return ""
        parts = []
        for cid in collection_index_ids:
            if coll := self._collections.get(cid):
                name = getattr(coll, "name", coll.get("name", "")) if isinstance(coll, dict) else coll.name
                summary = getattr(coll, "summary", coll.get("summary", "")) if isinstance(coll, dict) else coll.summary
                parts.append(f"Collection Index (id={cid}, name={name}): {summary}")
        return "\n".join(parts)

    # -- Files -----------------------------------------------------------------

    def create_file(
        self,
        filename: str,
        file_obj: BytesIO,
        team_id: int,
        content_type: str | None = None,
        purpose: str | None = None,
    ) -> SimpleNamespace:
        record = SimpleNamespace(
            filename=filename,
            team_id=team_id,
            content_type=content_type,
            purpose=purpose,
            id=len(self.files_created) + 1,
        )
        self.files_created.append(record)
        return record

    def attach_files_to_chat(
        self,
        chat: Any,
        attachment_type: str,
        files: list[Any],
    ) -> None:
        self.attached_files.append(
            {
                "chat": chat,
                "attachment_type": attachment_type,
                "files": files,
            }
        )

    # -- Participant -----------------------------------------------------------

    def get_participant_schedules(
        self,
        participant: Any,
        experiment_id: int,
        as_dict: bool = True,
        as_timezone: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict] | list[str]:
        self.schedule_lookups.append(
            {
                "participant": participant,
                "experiment_id": experiment_id,
                "as_dict": as_dict,
                "as_timezone": as_timezone,
                "include_inactive": include_inactive,
            }
        )
        return []

    # -- Assistants ------------------------------------------------------------

    def get_assistant(self, assistant_id: int) -> Any | None:
        return self._assistants.get(assistant_id)
