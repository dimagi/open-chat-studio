from typing import Any, Self

from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ParticipantData
from apps.utils.time import pretty_date


class PromptTemplateContext:
    def __init__(self, session, source_material_id: int = None, collection_id: int = None):
        self.session = session
        self.source_material_id = source_material_id
        self.collection_id = collection_id
        self.context_cache = {}
        self.participant_data_proxy = ParticipantDataProxy(self.session)

    @property
    def factories(self):
        return {
            "source_material": self.get_source_material,
            "participant_data": self.get_participant_data,
            "current_datetime": self.get_current_datetime,
            "media": self.get_media_summaries,
        }

    def get_context(self, variables: list[str]) -> dict:
        context = {}
        for key, factory in self.factories.items():
            # allow partial matches to support format specifiers
            if any(key in var for var in variables):
                if key not in self.context_cache:
                    self.context_cache[key] = factory()
                context[key] = self.context_cache[key]
        return context

    def get_source_material(self):
        from apps.experiments.models import SourceMaterial

        if self.source_material_id is None:
            return ""

        try:
            return SourceMaterial.objects.get(id=self.source_material_id).material
        except SourceMaterial.DoesNotExist:
            return ""

    def get_media_summaries(self):
        """
        Example output:
        * File (27): summary1
        * File (28): summary2
        """
        from apps.documents.models import Collection

        try:
            repo = Collection.objects.get(id=self.collection_id)
            file_info = repo.files.values_list("id", "summary", "content_type")
            return "\n".join(
                [
                    f"* File (id={id}, content_type={content_type}): {summary}\n"
                    for id, summary, content_type in file_info
                ]
            )
        except Collection.DoesNotExist:
            return ""

    def get_participant_data(self):
        if self.is_unauthorized_participant:
            data = ""
        else:
            data = self.participant_data_proxy.get() or ""
        return SafeAccessWrapper(data)

    def get_current_datetime(self):
        return pretty_date(timezone.now(), self.participant_data_proxy.get_timezone())

    @property
    def is_unauthorized_participant(self):
        """Returns `true` if a participant is unauthorized. A participant is considered authorized when the
        following conditions are met:
        For web channels:
        - They are a platform user
        All other channels:
        - Always True, since the external channel handles authorization
        """
        return self.session.experiment_channel.platform == ChannelPlatform.WEB and self.session.participant.user is None


class SafeAccessWrapper(dict):
    """Allow access to nested data structures without raising exceptions.

    This class wraps around a data structure and allows access to its elements
    without raising exceptions. If the element does not exist, it returns an
    empty string. This is useful when formatting strings with nested data
    structures, where some elements may not exist.

    Attributes can be accessed using dot notation, and items can be accessed
    using dot notation or square brackets. For accessing items, the key must not be quoted
    and can be an integer or a string. Slicing is not supported.

    Examples:
    >>> data = {"name": "John Doe", "age": 19, "tasks": [{"name": "Task 1", "status": "completed"}]}
    >>> data = SafeAccessWrapper(data)
    >>> print("{data.name}!".format(data=data))
    John Doe!
    >>> print("{data.tasks[0].name}".format(data=data))
    Task 1
    >>> print("{data.address}".format(data=data))
    """

    def __init__(self, data: Any):
        self.__data = data
        super().__init__(self, __data=data)

    def __getitem__(self, key):
        if isinstance(self.__data, list | str):
            try:
                return SafeAccessWrapper(self.__data[int(key)])
            except (IndexError, ValueError):
                return SafeAccessWrapper(None)
        if isinstance(self.__data, dict):
            return SafeAccessWrapper(self.__data.get(key, None))

        if isinstance(self.__data, int):
            return EMPTY

        try:
            return SafeAccessWrapper(self.__data[key])
        except (IndexError, TypeError):
            return EMPTY

    def __getattr__(self, key):
        if key.startswith("__"):
            # don't try and wrap special methods
            raise AttributeError(key)

        if isinstance(self.__data, dict):
            return SafeAccessWrapper(self.__data.get(key, ""))
        elif isinstance(self.__data, list | str):
            try:
                return SafeAccessWrapper(self.__data[int(key)])
            except (IndexError, ValueError):
                return EMPTY

        try:
            return SafeAccessWrapper(getattr(self.__data, key))
        except AttributeError:
            return EMPTY

    def __str__(self):
        return str(self.__data) if self.__data is not None else ""

    def __repr__(self):
        return f"SafeAccessWrapper({self.__data!r})"

    def __eq__(self, other):
        if isinstance(other, SafeAccessWrapper):
            return self.__data == other.data
        return self.__data == other


EMPTY = SafeAccessWrapper(None)


class ParticipantDataProxy:
    """Allows multiple access without needing to re-fetch from the DB"""

    def __init__(self, experiment_session):
        self.session = experiment_session
        self._participant_data = None
        self._scheduled_messages = None

    @classmethod
    def from_state(cls, pipeline_state) -> Self:
        # using `.get` here for the sake of tests. In practice the session should always be present
        return cls(pipeline_state.get("experiment_session"))

    def _get_db_object(self):
        if not self._participant_data:
            self._participant_data, _ = ParticipantData.objects.get_or_create(
                participant_id=self.session.participant_id,
                experiment_id=self.session.experiment_id,
                team_id=self.session.team_id,
            )
        return self._participant_data

    def get(self):
        data = self._get_db_object().data
        return self.session.participant.global_data | data

    def set(self, data):
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")
        participant_data = self._get_db_object()
        participant_data.data = data
        participant_data.save(update_fields=["data"])

        self.session.participant.update_name_from_data(data)

    def get_schedules(self):
        """
        Returns all active scheduled messages for the participant in the current experiment session.
        """
        if self._scheduled_messages is None:
            from apps.events.models import ScheduledMessage

            experiment = self.session.experiment_id
            participant = self.session.participant_id
            team = self.session.experiment.team
            messages = (
                ScheduledMessage.objects.filter(
                    experiment_id=experiment,
                    participant_id=participant,
                    team=team,
                    is_complete=False,
                    cancelled_at=None,
                )
                .select_related("action")
                .order_by("created_at")
            )
            self._scheduled_messages = [message.as_dict() for message in messages]
        return self._scheduled_messages

    def get_timezone(self):
        """Returns the participant's timezone"""
        participant_data = self._get_db_object()
        return participant_data.data.get("timezone")
