import uuid
from functools import wraps

from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.decorators import ENFORCES_TEAM_AUTH_ATTR


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
        # lookups to it. Mark them so the team-auth guard recognises it. Callers stack
        # `login_and_team_required` + a `chat.view_chat` permission check on top; this decorator
        # only resolves the objects.
        setattr(decorated_view, ENFORCES_TEAM_AUTH_ATTR, True)
        return decorated_view

    return decorator
