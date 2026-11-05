"""Creates and deletes the Playwright E2E test data for the `e2e_seed` command. Only for DEBUG or CI."""

import uuid
from typing import Literal, NotRequired, TypedDict, get_args

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse
from field_audit.models import AuditAction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.pipelines.models import Pipeline
from apps.service_providers import mock_llm
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams import backends
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.users.models import CustomUser
from apps.utils.deletion import delete_object_with_auditing_of_related_objects
from apps.utils.factories.experiment import (
    ChatbotFactory,
    ChatMessageFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

# Every user `create_e2e_data` makes has an email on this domain, which is how `delete_e2e_data` finds them.
E2E_EMAIL_DOMAIN = "e2e.test"
E2E_PASSWORD = "e2epassword"
# Each state includes the ones before it.
State = Literal["team", "llm_provider", "chatbot", "session"]
STATES: tuple[State, ...] = get_args(State)
USER_MESSAGE = "Reply with a short greeting."


class SessionCookie(TypedDict):
    name: str
    value: str


class E2EData(TypedDict):
    """What a test needs. The fields after `session_cookie` exist only from the state that adds them."""

    email: str
    password: str
    team_slug: str
    team_url: str
    session_cookie: SessionCookie
    llm_provider_name: NotRequired[str]
    chatbot_name: NotRequired[str]
    chatbot_url: NotRequired[str]
    user_message: NotRequired[str]
    session_url: NotRequired[str]


def create_e2e_data(state: State, base_url: str) -> E2EData:
    """Create a new user who owns a new team, plus the data for `state`, and return what a test needs."""
    backends.create_default_groups()
    key = uuid.uuid4().hex[:12]
    email = f"{key}@{E2E_EMAIL_DOMAIN}"
    team = TeamFactory.create(name=f"E2E {key}", slug=f"e2e-{key}")
    with current_team(team):
        user = UserFactory.create(username=email, email=email, set_password=E2E_PASSWORD)
        backends.make_user_team_owner(team=team, user=user)
        data: E2EData = {
            "email": email,
            "password": E2E_PASSWORD,
            "team_slug": team.slug,
            "team_url": f"/a/{team.slug}",
            "session_cookie": _log_in(user),
        }
        if state == "team":
            return data

        provider, model = _create_llm_provider(team=team, base_url=base_url)
        data["llm_provider_name"] = provider.name
        if state == "llm_provider":
            return data

        chatbot = _create_chatbot(team=team, user=user, provider=provider, model=model)
        data["chatbot_name"] = chatbot.name
        data["chatbot_url"] = reverse(viewname="chatbots:single_chatbot_home", args=[team.slug, chatbot.id])
        if state == "chatbot":
            return data

        session = _create_session(team=team, user=user, chatbot=chatbot)
        data["user_message"] = USER_MESSAGE
        data["session_url"] = reverse(
            viewname="chatbots:chatbot_session_view", args=[team.slug, chatbot.public_id, session.external_id]
        )
        return data


def _log_in(user: CustomUser) -> SessionCookie:
    """Start a logged-in session for the user, so a test can skip the login form."""
    client = Client()
    client.force_login(user)
    return {"name": settings.SESSION_COOKIE_NAME, "value": client.cookies[settings.SESSION_COOKIE_NAME].value}


def _create_llm_provider(team: Team, base_url: str) -> tuple[LlmProvider, LlmProviderModel]:
    provider = LlmProviderFactory.create(
        team=team,
        name="Mock OpenAI",
        config={"openai_api_key": "mock", "openai_api_base": mock_llm.base_url(base_url)},
    )
    # The mock answers to any model name; a team-scoped one keeps the test off the global model list.
    model = LlmProviderModelFactory.create(
        team=team, name=mock_llm.MODEL_NAME, max_token_limit=mock_llm.MODEL_TOKEN_LIMIT
    )
    return provider, model


def _create_chatbot(team: Team, user: CustomUser, provider: LlmProvider, model: LlmProviderModel) -> Experiment:
    pipeline = Pipeline.create_default(
        team=team, name="E2E Pipeline", llm_provider_id=provider.id, llm_provider_model=model
    )
    return ChatbotFactory.create(team=team, owner=user, name="E2E Chatbot", pipeline=pipeline)


def _create_session(team: Team, user: CustomUser, chatbot: Experiment) -> ExperimentSession:
    participant = ParticipantFactory.create(team=team, identifier=user.email, user=user, platform=ChannelPlatform.WEB)
    session = ExperimentSessionFactory.create(
        team=team,
        experiment=chatbot,
        participant=participant,
        experiment_channel=ExperimentChannel.objects.get_team_web_channel(team),
    )
    ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content=USER_MESSAGE)
    ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hello!")
    return session


def delete_e2e_data() -> None:
    """Delete the users `create_e2e_data` made and the teams only they belong to."""
    user_model = get_user_model()
    e2e_users = user_model.objects.filter(email__endswith=f"@{E2E_EMAIL_DOMAIN}")
    other_users = user_model.objects.exclude(email__endswith=f"@{E2E_EMAIL_DOMAIN}")
    teams = Team.objects.filter(membership__user__in=e2e_users).exclude(membership__user__in=other_users)
    for team in teams.distinct():
        with transaction.atomic(), current_team(team):
            delete_object_with_auditing_of_related_objects(team)
    e2e_users.delete(audit_action=AuditAction.IGNORE)
