from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.utils.time import pretty_date


class PromptTemplateContext:
    def __init__(self, session, source_material_id):
        self.session = session
        self.source_material_id = source_material_id
        self.context_cache = {}

    @property
    def factories(self):
        return {
            "source_material": self.get_source_material,
            "participant_data": self.get_participant_data,
            "current_datetime": self.get_current_datetime,
        }

    def get_context(self, variables: list[str]):
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

        try:
            return SourceMaterial.objects.get(id=self.source_material_id).material
        except SourceMaterial.DoesNotExist:
            return ""

    def get_participant_data(self):
        if self.is_unauthorized_participant:
            data = ""
        else:
            data = self.session.get_participant_data(use_participant_tz=True) or ""
        return SafeAccessWrapper(data)

    def get_current_datetime(self):
        return pretty_date(timezone.now(), self.session.get_participant_timezone())

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


class SafeAccessWrapper:
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

    def __init__(self, data):
        self.data = data

    def __getattribute__(self, item):
        if item and item.startswith("__"):
            return EMPTY
        return super().__getattribute__(item)

    def __getitem__(self, key):
        if isinstance(self.data, list | str):
            try:
                return SafeAccessWrapper(self.data[int(key)])
            except (IndexError, ValueError):
                return SafeAccessWrapper(None)
        if isinstance(self.data, dict):
            return SafeAccessWrapper(self.data.get(key, None))

        if isinstance(self.data, int):
            return EMPTY

        try:
            return SafeAccessWrapper(self.data[key])
        except (IndexError, TypeError):
            return EMPTY

    def __getattr__(self, key):
        if isinstance(self.data, dict):
            return SafeAccessWrapper(self.data.get(key, ""))
        elif isinstance(self.data, list | str):
            try:
                return SafeAccessWrapper(self.data[int(key)])
            except (IndexError, ValueError):
                return EMPTY

        try:
            return SafeAccessWrapper(getattr(self.data, key))
        except AttributeError:
            return EMPTY

    def __str__(self):
        return str(self.data) if self.data is not None else ""


EMPTY = SafeAccessWrapper(None)
