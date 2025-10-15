import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from apps.channels.models import ExperimentChannel
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.utils.factories.team import MembershipFactory, TeamFactory, get_test_user_groups
from apps.utils.factories.user import UserFactory

User = get_user_model()


@pytest.fixture()
def user():
    """Create a test user"""
    return UserFactory()


@pytest.fixture()
def team(user):
    """Create a test team with the user as a member"""
    team = TeamFactory()
    MembershipFactory(team=team, user=user, groups=get_test_user_groups)
    return team


@pytest.fixture()
def experiment_team():
    """Create a separate team for multi-team testing"""
    return TeamFactory(name="Test Experiment", slug="test-experiment")


@pytest.fixture()
def experiment(team, user):
    """Create a test experiment"""
    return Experiment.objects.create(
        name="Test Experiment",
        description="A test experiment for dashboard testing",
        team=team,
        owner=user,
        prompt_text="Test prompt",
        temperature=0.7,
    )


@pytest.fixture()
def participant(team):
    """Create a test participant"""
    return Participant.objects.create(
        name="Test Participant", identifier="test.participant@example.com", team=team, platform="web"
    )


@pytest.fixture()
def experiment_channel(team):
    """Create a test experiment channel"""
    return ExperimentChannel.objects.create(name="Test Channel", platform="web", team=team)


@pytest.fixture()
def experiment_session(experiment, participant, team):
    """Create a test experiment session"""
    return ExperimentSession.objects.create(experiment=experiment, participant=participant, team=team, status="active")


@pytest.fixture()
def chat(team, experiment_session):
    """Create a test chat and link it to the session"""
    chat = Chat.objects.create(name="Test Chat", team=team)
    experiment_session.chat = chat
    experiment_session.save()
    return chat


@pytest.fixture()
def authenticated_client(user, team, client):
    """Create an authenticated client with team context"""
    client.force_login(user)

    # Add team context to the session or request
    # This simulates the team middleware setting the team
    session = client.session
    session["team_id"] = team.id
    session.save()

    return client


@pytest.fixture()
def client():
    """Create a test client"""
    return Client()


@pytest.fixture()
def sample_messages(chat):
    """Create sample messages for testing"""
    messages = []

    # Create human message
    human_msg = ChatMessage.objects.create(
        chat=chat, message_type=ChatMessageType.HUMAN, content="Hello, this is a human message"
    )
    messages.append(human_msg)

    # Create AI message
    ai_msg = ChatMessage.objects.create(
        chat=chat, message_type=ChatMessageType.AI, content="Hello, this is an AI response"
    )
    messages.append(ai_msg)

    # Create system message
    system_msg = ChatMessage.objects.create(
        chat=chat, message_type=ChatMessageType.SYSTEM, content="System message for testing"
    )
    messages.append(system_msg)

    return messages


@pytest.fixture()
def completed_session(experiment, participant, team):
    """Create a completed experiment session"""
    start_time = timezone.now() - timezone.timedelta(hours=2)
    end_time = timezone.now() - timezone.timedelta(hours=1)

    session = ExperimentSession.objects.create(
        experiment=experiment,
        participant=participant,
        team=team,
        status="complete",
        created_at=start_time,
        ended_at=end_time,
    )

    # Create chat for the session
    chat = Chat.objects.create(team=team, name="Completed Chat")
    session.chat = chat
    session.save()

    return session


@pytest.fixture()
def multiple_experiments(team, user):
    """Create multiple experiments for testing"""
    experiments = []

    for i in range(3):
        exp = Experiment.objects.create(
            name=f"Test Experiment {i + 1}",
            description=f"Test experiment {i + 1} for dashboard testing",
            team=team,
            owner=user,
            prompt_text=f"Test prompt {i + 1}",
            temperature=0.7,
        )
        experiments.append(exp)

    return experiments


@pytest.fixture()
def multiple_participants(team):
    """Create multiple participants for testing"""
    participants = []

    for i in range(5):
        participant = Participant.objects.create(
            name=f"Test Participant {i + 1}",
            identifier=f"test.participant{i + 1}@example.com",
            team=team,
            platform="web",
        )
        participants.append(participant)

    return participants


@pytest.fixture()
def multiple_channels(team):
    """Create multiple channels for testing"""
    channels = []
    platforms = ["web", "whatsapp", "telegram", "slack"]

    for platform in platforms:
        channel = ExperimentChannel.objects.create(
            name=f"Test {platform.title()} Channel", platform=platform, team=team
        )
        channels.append(channel)

    return channels


@pytest.fixture()
def dashboard_test_data(team, user, multiple_experiments, multiple_participants, multiple_channels):
    """Create comprehensive test data for dashboard testing"""
    sessions = []
    chats = []
    messages = []

    # Create sessions for each experiment-participant combination
    for exp_idx, experiment in enumerate(multiple_experiments):
        for part_idx, participant in enumerate(multiple_participants):
            # Create every other session as completed
            is_completed = (exp_idx + part_idx) % 2 == 0

            session = ExperimentSession.objects.create(
                experiment=experiment,
                participant=participant,
                team=team,
                status="complete" if is_completed else "active",
                experiment_channel=multiple_channels[exp_idx],
                ended_at=timezone.now() if is_completed else None,
            )
            sessions.append(session)

            # Create chat for session
            chat = Chat.objects.create(team=team, name=f"Chat {exp_idx}-{part_idx}")
            session.chat = chat
            session.save()
            chats.append(chat)

            # Create messages for each chat
            num_messages = (exp_idx + 1) * 2  # Variable message count
            for msg_idx in range(num_messages):
                message_type = ChatMessageType.HUMAN if msg_idx % 2 == 0 else ChatMessageType.AI
                message = ChatMessage.objects.create(
                    chat=chat, message_type=message_type, content=f"Test message {msg_idx} in chat {exp_idx}-{part_idx}"
                )
                messages.append(message)

    return {
        "experiments": multiple_experiments,
        "participants": multiple_participants,
        "channels": multiple_channels,
        "sessions": sessions,
        "chats": chats,
        "messages": messages,
    }
