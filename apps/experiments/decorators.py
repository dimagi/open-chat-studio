import uuid
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.experiments.models import Experiment, ExperimentSession


def experiment_session_view(view_func):
    """Resolves the experiment and session from the URL onto the request.

    Sets ``request.experiment`` and ``request.experiment_session``, both scoped to
    ``request.team``, and 404s if either is missing. It runs no authorisation of its own:
    callers stack `login_and_team_required` and an access check on top.
    """

    @wraps(view_func)
    def decorated_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str, **kwargs):
        if not request.team:
            raise Http404

        request.experiment = get_object_or_404(Experiment.objects.get_all(), public_id=experiment_id, team=request.team)
        try:
            request.experiment_session = ExperimentSession.objects.select_related("participant", "chat").get(
                experiment=request.experiment,
                external_id=session_id,
                team=request.team,
            )
        except ExperimentSession.DoesNotExist:
            raise Http404() from None

        return view_func(request, team_slug, experiment_id, session_id, **kwargs)

    # Deliberately *not* stamped with ENFORCES_TEAM_AUTH_ATTR: resolving team-scoped objects
    # is not authorisation. Stamping it would make apps/teams/tests/test_view_auth_guard.py
    # accept a view that only resolves the session, hiding a missing `login_and_team_required`.
    return decorated_view


def require_transcript_access(view):
    """Gates a single session's transcript. Apply below `experiment_session_view`.

    `chat.view_chat` grants any of the team's transcripts. Without it, a participant may
    still read their own: `Chatbot Admin` carries no `chat` permissions (see
    apps/teams/backends.py), and the builder loop is to chat to your bot through the widget
    on its own page and then read that conversation back.

    Only for views scoped to one session — anything that navigates between sessions (e.g.
    `paginate_session`) must require `chat.view_chat` outright, since the session it moves
    to is not the one this check ran against.
    """

    @wraps(view)
    def _inner(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        # `user_id` is None for anonymous participants, and `AnonymousUser.id` is None too,
        # so the ownership branch must never compare two Nones.
        participant_user_id = request.experiment_session.participant.user_id
        is_own_session = participant_user_id is not None and participant_user_id == request.user.id
        if is_own_session or request.user.has_perm("chat.view_chat"):
            return view(request, *args, **kwargs)
        raise PermissionDenied

    return _inner
