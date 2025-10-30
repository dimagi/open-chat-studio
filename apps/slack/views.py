from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from slack_bolt.adapter.django.handler import to_bolt_request, to_django_response

from apps.slack.models import SlackOAuthState
from apps.slack.slack_app import app, handler
from apps.teams.decorators import login_and_team_required
from apps.web.waf import WafRule, waf_allow

from .const import INSTALLATION_CONFIG


@login_and_team_required
def slack_install(request, team_slug):
    """This view produced a redirect response which will take the user to the Slack
    App Installation page
    """
    bolt_request = to_bolt_request(request)
    bolt_request.context["team"] = request.team
    bolt_request.context[INSTALLATION_CONFIG] = {}
    bolt_resp = app.oauth_flow.handle_installation(bolt_request)
    return bolt_resp_to_django_resp(request, bolt_resp)


def slack_oauth_redirect(request):
    """Global view that slack redirects to after installation. This view can not be
    dynamic i.e. it can't be team specific which is why we save the team in the
    state and retrieve it here.
    """
    bolt_request = to_bolt_request(request)
    state = request.GET["state"]
    if state:
        state_obj = SlackOAuthState.objects.filter(state=state).first()
        if state_obj:
            bolt_request.context.update(state_obj.get_request_context())

    bolt_resp = app.oauth_flow.handle_callback(bolt_request)
    return bolt_resp_to_django_resp(request, bolt_resp)


@waf_allow(WafRule.SizeRestrictions_BODY)
@csrf_exempt
def slack_events_handler(request):
    # see `slack_listeners.py`
    return handler.handle(request)


def bolt_resp_to_django_resp(request, bolt_resp):
    add_messages(request, bolt_resp)
    return to_django_response(bolt_resp)


def add_messages(request, bolt_resp):
    if hasattr(bolt_resp, "_messages"):
        for level, message in bolt_resp._messages:
            messages.add_message(request, level, message)
