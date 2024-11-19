from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.utils.time import pretty_date


class ContextError(Exception):
    pass


class PromptTemplateContext:
    def __init__(self, session, source_material_id):
        self.session = session
        self.source_material_id = source_material_id
        self.context_cache = {}
        self.factories = {
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
            return SourceMaterial.objects.get(id=self.source_material_id)
        except SourceMaterial.DoesNotExist:
            raise ContextError(f"Source material with id {self.source_material_id} does not exist")

    def get_participant_data(self):
        if self.is_unauthorized_participant:
            return ""
        return self.session.get_participant_data(use_participant_tz=True) or ""

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
