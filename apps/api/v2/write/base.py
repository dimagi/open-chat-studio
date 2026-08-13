"""Shared plumbing for the v2 chatbot write endpoints.

Ships ahead of the sub-resource views that consume it (#4140-#4145).
"""

from apps.api.permissions import BASE_PERMISSION_CLASSES, RequiresTeamPermission
from apps.api.v2.lookups import get_working_chatbot
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope


class ChatbotCompositionPermission(RequiresTeamPermission):
    """Editing a chatbot's composition is a *change* to the chatbot, whatever the verb.

    Deleting a pipeline node is not deleting the chatbot, so the stock ``DjangoModelPermissions``
    verb->permission map (which would demand ``delete_experiment``) is wrong for the sub-resources
    under ``/chatbots/{id}/``. The top-level chatbot resource keeps the stock map.
    """

    required_permissions = ["experiments.change_experiment"]


class ChatbotWriteMixin:
    """Auth and chatbot resolution for the sub-resources under ``/chatbots/{id}/``.

    Locking is deliberately *not* done here: the pipeline façade locks the ``Pipeline`` row and the
    trigger endpoints lock nothing, so the lock target belongs to each view.
    """

    # Built on BASE_PERMISSION_CLASSES so the read-only API-key gate survives (ADR-0021).
    permission_classes = [
        *BASE_PERMISSION_CLASSES,
        ChatbotCompositionPermission,
        TokenHasOAuthResourceScope,
    ]
    # TokenHasOAuthResourceScope derives chatbots:read for safe methods, chatbots:write otherwise.
    required_scopes = ["chatbots"]

    def get_chatbot(self) -> Experiment:
        """The working (draft) chatbot named by the URL, scoped to the request's team."""
        return get_working_chatbot(self.request.team, self.kwargs["id"])
