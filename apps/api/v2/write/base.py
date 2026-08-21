"""Shared plumbing for the v2 chatbot write endpoints.

Ships ahead of the sub-resource views that consume it (#4140-#4145).
"""

from apps.api.permissions import RequiresTeamPermission


class ChatbotCompositionPermission(RequiresTeamPermission):
    """Editing a chatbot's composition is a *change* to the chatbot, whatever the verb.

    Deleting a pipeline node is not deleting the chatbot, so the stock ``DjangoModelPermissions``
    verb->permission map (which would demand ``delete_experiment``) is wrong for the sub-resources
    under ``/chatbots/{id}/``. The top-level chatbot resource keeps the stock map.
    """

    required_permissions = ["experiments.change_experiment"]
