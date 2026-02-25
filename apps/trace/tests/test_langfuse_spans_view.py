from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from apps.service_providers.models import TraceProviderType
from apps.trace.models import TraceStatus
from apps.trace.views import TraceLangufuseSpansView
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory, ExperimentFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory
from apps.utils.factories.traces import TraceFactory

LANGFUSE_TRACE_ID = "lf-trace-abc123"
LANGFUSE_TRACE_URL = "https://cloud.langfuse.com/project/xxx/traces/lf-trace-abc123"


def _make_observation(obs_id, name, level="DEFAULT", parent_id=None):
    return SimpleNamespace(
        id=obs_id,
        name=name,
        level=level,
        type="SPAN",
        status_message=None,
        input={"prompt": "test"},
        output={"response": "test"},
        latency=0.5,
        start_time=None,
        parent_observation_id=parent_id,
    )


class TestBuildChildMap:
    """Unit tests for tree-building logic — no DB needed."""

    def test_separates_root_and_child_observations(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        child = _make_observation("obs-2", "Child", parent_id="obs-1")
        result = view._build_child_map([root, child])
        assert result == {"obs-1": [child]}

    def test_multiple_children_under_same_parent(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        child_a = _make_observation("obs-2", "Child A", parent_id="obs-1")
        child_b = _make_observation("obs-3", "Child B", parent_id="obs-1")
        result = view._build_child_map([root, child_a, child_b])
        assert result == {"obs-1": [child_a, child_b]}

    def test_returns_plain_dict_not_defaultdict(self):
        from collections import defaultdict

        view = TraceLangufuseSpansView()
        result = view._build_child_map([])
        assert not isinstance(result, defaultdict)
        assert isinstance(result, dict)

    def test_observation_with_no_parent_not_in_map(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        result = view._build_child_map([root])
        assert result == {}


@pytest.mark.django_db()
class TestTraceLangufuseSpansView:
    @pytest.fixture()
    def trace_provider(self, team):
        return TraceProviderFactory(
            team=team,
            type=TraceProviderType.langfuse,
            config={"public_key": "pk-test", "secret_key": "sk-test", "host": "https://cloud.langfuse.com"},
        )

    @pytest.fixture()
    def experiment(self, team, trace_provider):
        return ExperimentFactory(team=team, trace_provider=trace_provider)

    @pytest.fixture()
    def output_message(self, team):
        return ChatMessageFactory(
            chat=ChatFactory(team=team),
            metadata={
                "trace_info": [
                    {
                        "trace_id": LANGFUSE_TRACE_ID,
                        "trace_url": LANGFUSE_TRACE_URL,
                        "trace_provider": "langfuse",
                    }
                ]
            },
        )

    @pytest.fixture()
    def trace(self, team, experiment, output_message):
        return TraceFactory(team=team, experiment=experiment, output_message=output_message, status=TraceStatus.SUCCESS)

    def _url(self, team, trace):
        return reverse("trace:trace_langfuse_spans", args=[team.slug, trace.pk])

    def test_no_langfuse_provider_returns_not_available(self, client, team, user):
        """Experiment has no trace_provider: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=None)
        output_message = ChatMessageFactory(chat=ChatFactory(team=team), metadata={})
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_no_langfuse_trace_info_returns_not_available(self, client, team, user, trace_provider):
        """Output message has no Langfuse trace_info: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        output_message = ChatMessageFactory(
            chat=ChatFactory(team=team),
            metadata={"trace_info": [{"trace_provider": "ocs", "trace_id": "123"}]},
        )
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_no_output_message_returns_not_available(self, client, team, user, trace_provider):
        """Trace has no output_message: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        trace = TraceFactory(team=team, experiment=experiment, output_message=None)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_none_trace_id_in_trace_info_returns_not_available(self, client, team, user, trace_provider):
        """trace_info has a Langfuse entry but trace_id is None: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        output_message = ChatMessageFactory(
            chat=ChatFactory(team=team),
            metadata={
                "trace_info": [{"trace_provider": "langfuse", "trace_id": None, "trace_url": LANGFUSE_TRACE_URL}]
            },
        )
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_langfuse_api_error_returns_error_partial(self, client, team, user, trace):
        """Langfuse API call fails: show error partial with fallback link."""
        client.force_login(user)
        with patch("apps.trace.views.get_langfuse_api_client") as mock_client_factory:
            mock_api = MagicMock()
            mock_api.trace.get.side_effect = Exception("API unreachable")
            mock_client_factory.return_value = mock_api
            response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_error" in response.content
        assert LANGFUSE_TRACE_URL.encode() in response.content

    def test_successful_fetch_renders_observations(self, client, team, user, trace):
        """Successful Langfuse fetch: render span tree with observation names."""
        root_obs = _make_observation("obs-1", "Pipeline Run")
        child_obs = _make_observation("obs-2", "LLM Call", parent_id="obs-1")
        mock_trace_data = MagicMock()
        mock_trace_data.observations = [root_obs, child_obs]

        client.force_login(user)
        with patch("apps.trace.views.get_langfuse_api_client") as mock_client_factory:
            mock_api = MagicMock()
            mock_api.trace.get.return_value = mock_trace_data
            mock_client_factory.return_value = mock_api
            response = client.get(self._url(team, trace))

        assert response.status_code == 200
        assert b"Pipeline Run" in response.content
        assert b"LLM Call" in response.content
        assert LANGFUSE_TRACE_URL.encode() in response.content
        mock_api.trace.get.assert_called_once_with(LANGFUSE_TRACE_ID)
