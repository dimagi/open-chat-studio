import uuid
from functools import wraps

from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from apps.experiments.models import Experiment, ExperimentSession, SessionStatus
from apps.teams.decorators import TeamAccessDenied

MAX_AGE = 180 * 24 * 60 * 60  # 6 months

CHAT_SESSION_ACCESS_SALT = "ocs.chat_session_access.salt"

CHAT_SESSION_ACCESS_COOKIE = "chat_session_access"


def experiment_session_view(allowed_states=None):
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

            if allowed_states and request.experiment_session.status not in allowed_states:
                return _redirect_for_state(request, team_slug)
            return view_func(request, team_slug, experiment_id, session_id, **kwargs)

        return decorated_view

    return decorator


def set_session_access_cookie(response, experiment, experiment_session):
    """Set the session access cookie on the response"""
    value = _get_access_cookie_data(experiment, experiment_session)
    value = signing.get_cookie_signer(salt=CHAT_SESSION_ACCESS_SALT).sign_object(value)
    response.set_cookie(
        CHAT_SESSION_ACCESS_COOKIE,
        value,
        max_age=MAX_AGE,
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    return response


def get_chat_session_access_cookie_data(request, fail_silently=False):
    try:
        return signing.get_cookie_signer(salt=CHAT_SESSION_ACCESS_SALT).unsign_object(
            request.COOKIES[CHAT_SESSION_ACCESS_COOKIE], max_age=MAX_AGE
        )
    except Exception as e:
        if fail_silently:
            return None
        raise e


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
        if request.user.is_authenticated:
            if request.experiment_session.participant.user_id == request.user.id:
                return view(request, *args, **kwargs)
            elif request.resolver_match.url_name in ["experiment_chat", "chatbot_chat"]:
                # Authenticated users should only be able to access the chat UI for their own sessions
                return redirect(
                    reverse(
                        "chatbots:chatbot_session_view",
                        args=[request.team.slug, request.experiment.public_id, request.experiment_session.external_id],
                    )
                )
            elif request.team_membership and request.user.has_perm("chat.view_chat"):
                return view(request, *args, **kwargs)

        try:
            access_value = get_chat_session_access_cookie_data(request)
        except (signing.BadSignature, KeyError):
            raise (TeamAccessDenied() if request.user.is_superuser else Http404()) from None

        if not _validate_access_cookie_data(request.experiment, request.experiment_session, access_value):
            raise TeamAccessDenied() if request.user.is_superuser else Http404()

        return view(request, *args, **kwargs)

    return _inner


def _get_access_cookie_data(experiment, experiment_session):
    return {
        "experiment_id": str(experiment.public_id),
        "session_id": str(experiment_session.external_id),
        "participant_id": experiment_session.participant_id,
        "user_id": experiment_session.participant.user_id,
    }


def _validate_access_cookie_data(experiment, experiment_session, access_data):
    return _get_access_cookie_data(experiment, experiment_session) == access_data


def _redirect_for_state(request, team_slug):
    view_args = [team_slug, request.experiment.public_id, request.experiment_session.external_id]
    match request.experiment_session.status:
        case SessionStatus.SETUP | SessionStatus.PENDING:
            return HttpResponseRedirect(reverse("experiments:start_session_from_invite", args=view_args))
        case SessionStatus.PENDING_PRE_SURVEY:
            return HttpResponseRedirect(reverse("experiments:experiment_pre_survey", args=view_args))
        case SessionStatus.ACTIVE:
            return HttpResponseRedirect(reverse("chatbots:chatbot_chat", args=view_args))
        case SessionStatus.PENDING_REVIEW:
            return HttpResponseRedirect(reverse("experiments:experiment_review", args=view_args))
        case SessionStatus.COMPLETE:
            return HttpResponseRedirect(reverse("experiments:experiment_complete", args=view_args))
        case _:
            messages.info(
                request,
                "Session was in an unknown/unexpected state. It may be old, or something may have gone wrong.",
            )
            return HttpResponseRedirect(reverse("chatbots:chatbot_session_view", args=view_args))
