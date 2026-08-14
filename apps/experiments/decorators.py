import uuid
from functools import wraps

from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.decorators import ENFORCES_TEAM_AUTH_ATTR, TeamAccessDenied


def experiment_session_view():
    def decorator(view_func):
        """
        Runs experiement permission checks, handles redirects, etc.
        """

        @wraps(view_func)
        def decorated_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str, **kwargs):
            if not request.team:
                raise Http404

            request.experiment = get_object_or_404(
                Experiment.objects.get_all(), public_id=experiment_id, team=request.team
            )
            try:
                request.experiment_session = ExperimentSession.objects.select_related("participant", "chat").get(
                    experiment=request.experiment,
                    external_id=session_id,
                    team=request.team,
                )
            except ExperimentSession.DoesNotExist:
                raise Http404() from None

            return view_func(request, team_slug, experiment_id, session_id, **kwargs)

        # These views are team-scoped: they require request.team and scope the experiment/session
        # lookups to it. Mark them so the team-auth guard recognises it.
        setattr(decorated_view, ENFORCES_TEAM_AUTH_ATTR, True)
        return decorated_view

    return decorator


def require_session_access(view):
    """View decorator for views that display an experiment session.

    Access is granted to the participant who owns the session and to team members with
    `chat.view_chat`. This decorator must be applied after the `experiment_session_view`
    decorator:

    @experiment_session_view()
    @require_session_access
    def my_view(request, team_slug, experiment_id, session_id):
        ...
    """

    @wraps(view)
    def _inner(request, *args, **kwargs):
        if request.user.is_authenticated:
            is_own_session = request.experiment_session.participant.user_id == request.user.id
            is_team_viewer = request.team_membership and request.user.has_perm("chat.view_chat")
            if is_own_session or is_team_viewer:
                return view(request, *args, **kwargs)

        raise TeamAccessDenied() if request.user.is_superuser else Http404()

    return _inner
