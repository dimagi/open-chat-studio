"""Which rows a chatbot-scoped export may serve.

One ``ChatbotScope`` is built per request and answers "which rows of model X" for every resource.
Bounded sets resolve to id lists, so a dependent filter is an indexed ``IN`` the planner estimates
well; the session-, chat-, message- and file-sized sets stay querysets, because ``pk__in`` on a
materialised list does not scale for a busy chatbot.
"""

from collections.abc import Sequence
from functools import cached_property

from django.apps import apps
from django.db.models import Model, Q, QuerySet

from apps.events.versioning import get_event_action_param_specs
from apps.teams.export.selection import expand_to_family, selected_experiment_ids


def _model(label: str) -> type[Model]:
    return apps.get_model(*label.split("."))


def _with_working_versions(model: type[Model], base: Q) -> Q:
    """``base``, plus the working version of every row it matches.

    A published row's ``working_version`` FK points at a row that must be exported too, or the target
    cannot resolve it. Working versions have the lower pk, so the manifest's pk ordering still serves
    them first.
    """
    return base | Q(pk__in=model._base_manager.filter(base).values("working_version_id"))


def _resolve(label: str, base: Q, *, versioned: bool = True) -> list[int]:
    """A bounded set as a sorted id list."""
    model = _model(label)
    if versioned:
        base = _with_working_versions(model, base)
    return sorted(model._base_manager.filter(base).values_list("pk", flat=True))


def _ids_from(label: str, base: Q, column: str) -> QuerySet:
    """A column of one model as a subquery, for use in ``pk__in=``."""
    return _model(label)._base_manager.filter(base).values(column)


class ChatbotScope:
    """The rows one team's chatbot allowlist may export."""

    def __init__(self, selected_ids: Sequence[int]):
        self.selected_ids = list(selected_ids)

    # --- class (a): owned by the chatbot, plus the participants who talked to it --------------

    @cached_property
    def experiment_ids(self) -> list[int]:
        """The selected chatbots and every version of each: published ones and archived ones."""
        return sorted(expand_to_family(self.selected_ids).values_list("pk", flat=True))

    @cached_property
    def sessions(self) -> QuerySet:
        return _ids_from("experiments.experimentsession", Q(experiment_id__in=self.experiment_ids), "pk")

    @cached_property
    def chats(self) -> QuerySet:
        # Through the session's forward FK rather than Chat's reverse OneToOne: a forward chain is a
        # clean semi-join, so the limit still pushes down.
        return _ids_from("experiments.experimentsession", Q(experiment_id__in=self.experiment_ids), "chat_id")

    @cached_property
    def chat_messages(self) -> QuerySet:
        return _ids_from("chat.chatmessage", Q(chat_id__in=self.chats), "pk")

    @cached_property
    def participants(self) -> QuerySet:
        """Participants reachable from the family. Three branches because ``ParticipantData`` and
        ``ScheduledMessage`` both hold a non-null participant FK and are scoped by experiment, so a
        participant reachable only through one of those still has to be exported."""
        family = self.experiment_ids
        return _ids_from(
            "experiments.participant",
            Q(pk__in=_ids_from("experiments.experimentsession", Q(experiment_id__in=family), "participant_id"))
            | Q(pk__in=_ids_from("experiments.participantdata", Q(experiment_id__in=family), "participant_id"))
            | Q(pk__in=_ids_from("events.scheduledmessage", Q(experiment_id__in=family), "participant_id")),
            "pk",
        )

    @cached_property
    def channel_ids(self) -> list[int]:
        """The family's own channels plus the team-level ones (web, API, evaluations, widget), which
        carry no experiment. A session pointing at another chatbot's channel -- the ``is_stale()``
        case -- loses the link; the FK is nullable, so it nulls rather than failing the import."""
        return _resolve(
            "bot_channels.experimentchannel",
            Q(experiment_id__in=self.experiment_ids) | Q(experiment__isnull=True),
            versioned=False,
        )

    # --- class (b): reached by what the chatbot uses -------------------------------------------

    @cached_property
    def pipeline_ids(self) -> list[int]:
        return _resolve(
            "pipelines.pipeline",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "pipeline_id"))
            | Q(pk__in=self._event_action_pipeline_ids),
        )

    @cached_property
    def event_actions_q(self) -> Q:
        """The family's event actions. Each trigger type holds a OneToOne to its action, so these are
        single-valued joins: no duplicates and no DISTINCT. A ScheduledMessage's action is the static
        or timeout trigger's action that created it."""
        family = self.experiment_ids
        return (
            Q(static_trigger__experiment_id__in=family)
            | Q(timeout_trigger__experiment_id__in=family)
            | Q(scheduled_trigger__experiment_id__in=family)
        )

    @cached_property
    def _event_action_pipeline_ids(self) -> list[int]:
        """Pipelines a ``pipeline_start`` event action names in its params. Which params carry an id
        is declared in ``apps.events.versioning``; read in Python because the set is bounded by the
        family's trigger count and a JSON scalar needs casting either way."""
        actions = _model("events.eventaction")._base_manager.filter(self.event_actions_q)
        ids = []
        for action_type, params in actions.values_list("action_type", "params"):
            for spec in get_event_action_param_specs(action_type):
                if spec.model_label.lower() != "pipelines.pipeline":
                    continue
                value = (params or {}).get(spec.param_name)
                if value:
                    ids.append(int(value))
        return ids

    @cached_property
    def node_ids(self) -> list[int]:
        return _resolve("pipelines.node", Q(pipeline_id__in=self.pipeline_ids))

    @cached_property
    def collection_ids(self) -> list[int]:
        node = _model("pipelines.node")
        return _resolve(
            "documents.collection",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "collection_id"))
            | Q(
                pk__in=node.collection_indexes.through.objects.filter(node_id__in=self.node_ids).values("collection_id")
            ),
        )

    @cached_property
    def files(self) -> QuerySet:
        """Files the chatbot reaches: its collections' files (``Collection.files`` and
        ``DocumentSource.files`` are both m2m through ``CollectionFile``, so one branch covers both),
        its chat attachments, and any synthetic voice sample. Stays a queryset -- the attachment
        branch grows with conversation volume."""
        attachment_files = _model("chat.chatattachment").files.through.objects.filter(
            chatattachment__chat_id__in=self.chats
        )
        base = (
            Q(pk__in=_ids_from("documents.collectionfile", Q(collection_id__in=self.collection_ids), "file_id"))
            | Q(pk__in=attachment_files.values("file_id"))
            | Q(pk__in=_ids_from("experiments.syntheticvoice", Q(pk__in=self.synthetic_voice_ids), "file_id"))
        )
        file_model = _model("files.file")
        return file_model._base_manager.filter(_with_working_versions(file_model, base)).values("pk")

    @cached_property
    def custom_action_ids(self) -> list[int]:
        return _resolve(
            "custom_actions.customaction",
            Q(
                pk__in=_ids_from(
                    "custom_actions.customactionoperation", Q(node_id__in=self.node_ids), "custom_action_id"
                )
            ),
            versioned=False,
        )

    @cached_property
    def source_material_ids(self) -> list[int]:
        return _resolve(
            "experiments.sourcematerial",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "source_material_id")),
        )

    @cached_property
    def consent_form_ids(self) -> list[int]:
        return _resolve(
            "experiments.consentform",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "consent_form_id")),
        )

    @cached_property
    def synthetic_voice_ids(self) -> list[int]:
        return _resolve(
            "experiments.syntheticvoice",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "synthetic_voice_id"))
            | Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "synthetic_voice_id")),
            versioned=False,
        )

    @cached_property
    def llm_provider_ids(self) -> list[int]:
        nodes = Q(pk__in=self.node_ids)
        collections = Q(pk__in=self.collection_ids)
        return _resolve(
            "service_providers.llmprovider",
            Q(pk__in=_ids_from("pipelines.node", nodes, "llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "contextualizer_llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "reranker_provider_id")),
            versioned=False,
        )

    @cached_property
    def llm_provider_model_ids(self) -> list[int]:
        return _resolve(
            "service_providers.llmprovidermodel",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "llm_provider_model_id"))
            | Q(pk__in=_ids_from("documents.collection", Q(pk__in=self.collection_ids), "contextualizer_llm_model_id")),
            versioned=False,
        )

    @cached_property
    def embedding_provider_model_ids(self) -> list[int]:
        return _resolve(
            "service_providers.embeddingprovidermodel",
            Q(pk__in=_ids_from("documents.collection", Q(pk__in=self.collection_ids), "embedding_provider_model_id")),
            versioned=False,
        )

    @cached_property
    def voice_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.voiceprovider",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "voice_provider_id"))
            | Q(
                pk__in=_ids_from("experiments.syntheticvoice", Q(pk__in=self.synthetic_voice_ids), "voice_provider_id")
            ),
            versioned=False,
        )

    @cached_property
    def messaging_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.messagingprovider",
            Q(pk__in=_ids_from("bot_channels.experimentchannel", Q(pk__in=self.channel_ids), "messaging_provider_id")),
            versioned=False,
        )

    @cached_property
    def auth_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.authprovider",
            Q(pk__in=_ids_from("custom_actions.customaction", Q(pk__in=self.custom_action_ids), "auth_provider_id"))
            | Q(
                pk__in=_ids_from(
                    "documents.documentsource", Q(collection_id__in=self.collection_ids), "auth_provider_id"
                )
            ),
            versioned=False,
        )

    @cached_property
    def trace_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.traceprovider",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "trace_provider_id")),
            versioned=False,
        )


def build_scope(team) -> ChatbotScope | None:
    """The scope for this team's export, or None when it exports everything.

    An empty allowlist means the whole team, so a chatbot created after the selection was saved is
    exportable exactly when no selection is active.
    """
    selected = selected_experiment_ids(team)
    return ChatbotScope(selected) if selected else None
