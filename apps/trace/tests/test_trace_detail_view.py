import pytest
from django.urls import reverse

from apps.trace.models import TraceStatus
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.traces import TraceFactory


def _detail_url(team, trace):
    return reverse("trace:trace_detail", args=[team.slug, trace.pk])


@pytest.fixture()
def experiment(team):
    return ExperimentFactory.create(team=team)


@pytest.fixture()
def session(team, experiment):
    return ExperimentSessionFactory.create(team=team, experiment=experiment)


@pytest.fixture()
def participant(team):
    return ParticipantFactory.create(team=team)


@pytest.mark.django_db()
class TestTraceDetailView:
    def test_renders_successfully(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=3090,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        assert response.status_code == 200

    def test_summary_cards_render_when_metrics_populated(
        self, anon_client, team, user, experiment, session, participant
    ):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=3090,
            n_turns=1,
            n_toolcalls=0,
            n_total_tokens=146,
            n_prompt_tokens=30,
            n_completion_tokens=116,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        body = response.content.decode()

        assert "TOTAL LATENCY" in body
        assert "TOKENS" in body
        assert "LLM TURNS" in body
        assert "TOOL CALLS" in body
        assert "3.09s" in body
        assert "146" in body
        assert "30 in" in body
        assert "116 out" in body
        # bg-primary/30 is the unique class used for the output portion of the token bar
        assert "bg-primary/30" in body

    def test_token_bar_omitted_when_total_tokens_null(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=1000,
            n_total_tokens=None,
            n_prompt_tokens=None,
            n_completion_tokens=None,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        body = response.content.decode()

        assert "TOKENS" in body
        assert "bg-primary/30" not in body

    def test_null_turns_renders_emdash_not_zero(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=1000,
            n_turns=None,
            n_toolcalls=0,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        body = response.content.decode()

        # The card has both LLM TURNS ("—" because null) and TOOL CALLS ("0" because zero).
        # Find the LLM TURNS section and assert it contains the em-dash.
        llm_turns_idx = body.index("LLM TURNS")
        tool_calls_idx = body.index("TOOL CALLS")
        # LLM TURNS comes before TOOL CALLS in the card
        assert llm_turns_idx < tool_calls_idx
        llm_turns_section = body[llm_turns_idx:tool_calls_idx]
        assert "—" in llm_turns_section

    def test_experiment_version_appears_in_pill(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=1000,
            experiment_version_number=3,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        assert "v3" in response.content.decode()

    def test_unreleased_version_shown_when_null(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=1000,
            experiment_version_number=None,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        assert "unreleased" in response.content.decode().lower()

    def test_status_badge_appears_in_latency_card(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=1000,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        body = response.content.decode()

        latency_idx = body.index("TOTAL LATENCY")
        tokens_idx = body.index("TOKENS")
        # Status badge ("Success") must appear inside the TOTAL LATENCY card,
        # i.e. between the TOTAL LATENCY label and the next card's TOKENS label.
        latency_card = body[latency_idx:tokens_idx]
        assert "Success" in latency_card
        assert "badge-success" in latency_card
