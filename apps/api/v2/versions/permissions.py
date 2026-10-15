"""Permission classes for the chatbot version endpoints."""

from apps.api.permissions import RequiresTeamPermission


class ChatbotVersionDeletePermission(RequiresTeamPermission):
    """Archiving a version really is a delete, so this one asks for ``delete_experiment``.

    The other routes under ``/chatbots/{id}/`` deliberately do not: removing a pipeline node is a
    change to the chatbot rather than a deletion of one. A version is an ``Experiment`` row of its
    own, and this hides it.
    """

    required_permissions = ["experiments.delete_experiment"]
