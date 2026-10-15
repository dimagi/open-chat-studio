"""Shared plumbing for the v2 chatbot write endpoints -- the settings surface and the pipeline façade."""

from typing import Any

from django.http import Http404
from rest_framework import exceptions, serializers
from rest_framework.metadata import SimpleMetadata
from rest_framework.request import clone_request

from apps.api.permissions import RequiresTeamPermission


class DescribesPatch(SimpleMetadata):
    """OPTIONS metadata that describes PATCH alongside PUT and POST.

    DRF describes only the verbs that replace a whole resource, so on a route offering PATCH and
    DELETE alone stock OPTIONS answers with no body at all. OPTIONS is how the agent this API is
    built for discovers what it may send, so such a route is one it cannot learn to call.
    """

    describes = ("PUT", "POST", "PATCH")

    def determine_actions(self, request, view) -> dict[str, dict]:
        actions: dict[str, dict] = {}
        for method in [method for method in self.describes if method in view.allowed_methods]:
            view.request = clone_request(request, method)
            try:
                if hasattr(view, "check_permissions"):
                    view.check_permissions(view.request)
                if method == "PUT" and hasattr(view, "get_object"):
                    view.get_object()
            except (exceptions.APIException, exceptions.PermissionDenied, Http404):
                # Same as DRF's own: a verb this caller may not use is left undescribed rather than
                # failing the whole OPTIONS response.
                pass
            else:
                actions[method] = self.get_serializer_info(view.get_serializer())
            finally:
                view.request = request
        return actions


class ChatbotCompositionPermission(RequiresTeamPermission):
    """Editing a chatbot's composition is a *change* to the chatbot, whatever the verb.

    Deleting a pipeline node is not deleting the chatbot, so the stock ``DjangoModelPermissions``
    verb->permission map (which would demand ``delete_experiment``) is wrong for the sub-resources
    under ``/chatbots/{id}/``. The top-level chatbot resource keeps it.
    """

    required_permissions = ["experiments.change_experiment"]


class RejectsUnwritableKeys:
    """Refuse a request body carrying keys this serializer does not declare, or declares read-only.

    DRF's default is to drop both silently, which for an agent is a 200 that wrote nothing. Hooked
    into ``to_internal_value`` rather than ``validate`` because that is where a serializer sees its
    own raw input wherever it is mounted -- ``validate`` would have to read ``initial_data``, which
    DRF sets on the root serializer alone.
    """

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            refused = sorted(set(data) - writable)
            if refused:
                accepted = ", ".join(sorted(writable))
                raise serializers.ValidationError(
                    {
                        key: (
                            f"Read-only field. Accepted here: {accepted}."
                            if key in self.fields
                            else f"Unrecognised field. Accepted here: {accepted}."
                        )
                        for key in refused
                    }
                )
        return super().to_internal_value(data)
