"""Shared plumbing for the v2 chatbot write endpoints -- the settings surface and the pipeline façade."""

from typing import Any

from rest_framework import serializers

from apps.api.permissions import RequiresTeamPermission


class ChatbotCompositionPermission(RequiresTeamPermission):
    """Editing a chatbot's composition is a *change* to the chatbot, whatever the verb.

    Deleting a pipeline node is not deleting the chatbot, so the stock ``DjangoModelPermissions``
    verb->permission map (which would demand ``delete_experiment``) is wrong for the sub-resources
    under ``/chatbots/{id}/``. The top-level chatbot resource keeps it.
    """

    required_permissions = ["experiments.change_experiment"]


class RejectsUnknownKeys:
    """Refuse a request body carrying keys this serializer does not declare.

    DRF's default is to drop them silently, which for an agent is a 200 that wrote nothing. Hooked
    into ``to_internal_value`` rather than ``validate`` because that is where a serializer sees its
    own raw input wherever it is mounted -- ``validate`` would have to read ``initial_data``, which
    DRF sets on the root serializer alone.
    """

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                accepted = ", ".join(sorted(self.fields))
                raise serializers.ValidationError(
                    {key: f"Unrecognised field. Accepted here: {accepted}." for key in unknown}
                )
        return super().to_internal_value(data)
