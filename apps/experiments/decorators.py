from functools import wraps

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.experiments.models import Experiment, ExperimentSession, SessionStatus


def experiment_session_view(allowed_states=None):
    def decorator(view_func):
        """
        Runs experiement permission checks, handles redirects, etc.
        """

        @wraps(view_func)
        def decorated_view(request, team_slug: str, experiment_id: str, session_id: str):
            request.experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
            request.experiment_session = get_object_or_404(
                ExperimentSession, experiment=request.experiment, public_id=session_id, team=request.team
            )

            if allowed_states and request.experiment_session.status not in allowed_states:
                return _redirect_for_state(request, request.experiment_session, team_slug)
            return view_func(request, team_slug, experiment_id, session_id)

        return decorated_view

    return decorator


def _redirect_for_state(request, experiment_session, team_slug):
    view_args = [team_slug, experiment_session.experiment.public_id, experiment_session.public_id]
    if experiment_session.status in [SessionStatus.SETUP, SessionStatus.PENDING]:
        return HttpResponseRedirect(reverse("experiments:start_experiment_session", args=view_args))
    elif experiment_session.status == SessionStatus.PENDING_PRE_SURVEY:
        return HttpResponseRedirect(reverse("experiments:experiment_pre_survey", args=view_args))
    elif experiment_session.status == SessionStatus.ACTIVE:
        return HttpResponseRedirect(reverse("experiments:experiment_chat", args=view_args))
    elif experiment_session.status == SessionStatus.PENDING_REVIEW:
        return HttpResponseRedirect(reverse("experiments:experiment_review", args=view_args))
    elif experiment_session.status == SessionStatus.COMPLETE:
        return HttpResponseRedirect(reverse("experiments:experiment_complete", args=view_args))
    else:
        messages.info(
            request, "Session was in an unknown/unexpected state." " It may be old, or something may have gone wrong."
        )
        return HttpResponseRedirect(reverse("experiments:experiment_session_view", args=view_args))
