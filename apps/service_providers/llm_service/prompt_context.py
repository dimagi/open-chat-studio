from typing import Any, Self

from django.utils import timezone

from apps.utils.time import pretty_date


class PromptTemplateContext:
    def __init__(
        self,
        session,
        source_material_id: int = None,
        collection_id: int = None,
        extra: dict = None,
        participant_data: dict = None,
    ):
        self.session = session
        self.source_material_id = source_material_id
        self.collection_id = collection_id
        self.extra = extra or {}
        self.context_cache = {}
        if participant_data is None:
            participant_data = session.participant_data_from_experiment
        self.participant_data_proxy = ParticipantDataProxy({"participant_data": participant_data}, self.session)

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
            if any(var.startswith(key) for var in variables):
                if key not in self.context_cache:
                    self.context_cache[key] = factory()
                context[key] = self.context_cache[key]

        # add any extra context provided
        for key, value in self.extra.items():
            if key not in context:
                context[key] = SafeAccessWrapper(value)
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
        * File (id=27, content_type=image/png): summary1
        * File (id=28, content_type=application/pdf): summary2
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
        data = self.participant_data_proxy.get() or {}
        if scheduled_messages := self.participant_data_proxy.get_schedules():
            data = {**data, "scheduled_messages": scheduled_messages}
        return SafeAccessWrapper(data)

    def get_current_datetime(self):
        return pretty_date(timezone.now(), self.participant_data_proxy.get_timezone())


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

    def __init__(self, pipeline_state: dict, experiment_session):
        self.session = experiment_session
        self.experiment = self.session.experiment if self.session else None
        self._participant_data = pipeline_state.setdefault("participant_data", {})
        self._scheduled_messages = None

    @classmethod
    def from_state(cls, pipeline_state) -> Self:
        return cls(pipeline_state, pipeline_state.get("experiment_session"))

    def get(self):
        """Returns the current participant's data as a dictionary."""
        return self.session.participant.global_data | self._participant_data

    def set(self, data):
        """Updates the current participant's data with the provided dictionary.
        This will overwrite any existing data."""
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")
        self._participant_data.update(data)
        self.session.participant.update_name_from_data(self._participant_data)

    def set_key(self, key: str, value: Any):
        """Set a single key in the participant data."""
        self._participant_data[key] = value
        self.session.participant.update_name_from_data(self._participant_data)

    def append_to_key(self, key: str, value: Any) -> list[Any]:
        """
        Append a value to a list at the specified key in the participant data. If the current value is not a list,
        it will convert it to a list before appending.
        """
        existing_data = self._participant_data
        value_at_key = existing_data.get(key, [])
        if not isinstance(value_at_key, list):
            value_at_key = [value_at_key]

        if isinstance(value, list):
            value_at_key.extend(value)
        else:
            value_at_key.append(value)

        existing_data[key] = value_at_key
        self.set(existing_data)
        return value_at_key

    def increment_key(self, key: str, increment: int = 1) -> int:
        """
        Increment a numeric value at the specified key in the participant data.
        If the current value is not a number, it will be initialized to 0 before incrementing.
        """
        existing_data = self._participant_data
        current_value = existing_data.get(key, 0)

        if not isinstance(current_value, int | float):
            current_value = 0

        new_value = current_value + increment
        existing_data[key] = new_value
        self.set(existing_data)
        return new_value

    def get_schedules(self):
        """
        Returns all active scheduled messages for the participant in the current chat session.
        """
        if self._scheduled_messages is None:
            self._scheduled_messages = self.session.participant.get_schedules_for_experiment(
                self.experiment, as_dict=True, as_timezone=self.get_timezone()
            )
        return self._scheduled_messages

    def get_timezone(self):
        """Returns the participant's timezone"""
        return self._participant_data.get("timezone")
