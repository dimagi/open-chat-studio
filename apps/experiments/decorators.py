from functools import wraps

from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.experiments.models import Experiment, ExperimentSession, SessionStatus

MAX_AGE = 180 * 24 * 60 * 60  # 6 months

CHAT_SESSION_ACCESS_SALT = "ocs.chat_session_access.salt"

CHAT_SESSION_ACCESS_COOKIE = "chat_session_access"


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


def set_session_access_cookie(view):
    """Decorator for view functions that should set the session access cookie.
    This decorator must be applied on a view that is also decorated with the
    `experiment_session_view` decorator (though the order doesn't matter):

    @experiment_session_view(...)
    @set_session_access_cookie
    def my_view(request, team_slug, experiment_id, session_id):
        ...
    """

    @wraps(view)
    def _inner(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        experiment_session = request.experiment_session
        value = _get_access_cookie_data(experiment_session)
        value = signing.get_cookie_signer(salt=CHAT_SESSION_ACCESS_SALT).sign_object(value)
        response.set_cookie(
            CHAT_SESSION_ACCESS_COOKIE,
            value,
            max_age=MAX_AGE,
            secure=True,
            httponly=True,
            samesite="Strict",
        )
        return response

    return _inner


def verify_session_access_cookie(view):
    """View decorator for views that provide public access to an experiment session.
    This decorator must be applied before the `experiment_session_view` decorator:

    @experiment_session_view(...)
    @verify_session_access_cookie
    def my_view(request, team_slug, experiment_id, session_id):
        ...
    """

    @wraps(view)
    def _inner(request, *args, **kwargs):
        if request.user.is_authenticated and (
            request.experiment_session.user_id == request.user.id or request.user.has_perm("chat.view_chat")
        ):
            return view(request, *args, **kwargs)

        try:
            access_value = signing.get_cookie_signer(salt=CHAT_SESSION_ACCESS_SALT).unsign_object(
                request.COOKIES[CHAT_SESSION_ACCESS_COOKIE], max_age=MAX_AGE
            )
        except (signing.BadSignature, KeyError):
            raise Http404()

        # access_data = json.loads(access_value)
        access_data = access_value
        if not _validate_access_cookie_data(request.experiment_session, access_data):
            raise Http404()

        return view(request, *args, **kwargs)

    return _inner


def _get_access_cookie_data(experiment_session):
    return {
        "experiment_id": str(experiment_session.experiment.public_id),
        "session_id": str(experiment_session.public_id),
        "participant_id": experiment_session.participant_id,
        "user_id": experiment_session.user_id,
    }


def _validate_access_cookie_data(experiment_session, access_data):
    return _get_access_cookie_data(experiment_session) == access_data


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
