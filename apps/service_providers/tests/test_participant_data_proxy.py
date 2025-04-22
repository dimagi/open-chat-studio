from unittest.mock import patch

import pytest

from apps.experiments.models import Participant, ParticipantData
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
class TestParticipantDataProxy:
    """Tests for the ParticipantDataProxy class that handles participant data access"""

    def test_initialization(self):
        """Test that ParticipantDataProxy initializes correctly"""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment)
        proxy = ParticipantDataProxy(session)

        assert proxy.session == session
        assert proxy.experiment == experiment
        assert proxy._participant_data is None
        assert proxy._scheduled_messages is None

    def test_from_state(self):
        """Test creating a ParticipantDataProxy from pipeline state"""
        session = ExperimentSessionFactory()
        pipeline_state = {"experiment_session": session}
        proxy = ParticipantDataProxy.from_state(pipeline_state)

        assert proxy.session == session
        assert proxy.experiment == session.experiment

    def test_from_state_with_missing_session(self):
        """Test handling when state doesn't have experiment_session"""
        pipeline_state = {}
        proxy = ParticipantDataProxy.from_state(pipeline_state)

        assert proxy.session is None
        assert proxy.experiment is None

    def test_get_db_object_creates_if_not_exists(self):
        """Test that _get_db_object creates a ParticipantData if it doesn't exist"""
        participant = ParticipantFactory()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)

        proxy = ParticipantDataProxy(session)

        # First call should create the object
        result = proxy._get_db_object()
        assert isinstance(result, ParticipantData)
        assert result.participant_id == participant.id
        assert result.experiment_id == experiment.id
        assert result.team_id == experiment.team_id

        # Second call should return the cached object
        assert proxy._get_db_object() is result

    def test_get_returns_merged_data(self):
        """Test that get() merges participant global data with participant data"""
        participant = ParticipantFactory(name="Test User")
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)

        # Create participant data with some data
        participant_data = ParticipantData.objects.create(
            participant=participant, experiment=experiment, team=experiment.team, data={"favorite_color": "blue"}
        )

        proxy = ParticipantDataProxy(session)
        proxy._participant_data = participant_data

        # Participant's global_data should include the name
        expected_data = {"name": "Test User", "favorite_color": "blue"}
        assert proxy.get() == expected_data

    def test_set_validates_data_type(self):
        """Test that set() validates the data type"""
        session = ExperimentSessionFactory()
        proxy = ParticipantDataProxy(session)

        with pytest.raises(ValueError, match="Data must be a dictionary"):
            proxy.set("not a dictionary")

    def test_set_updates_participant_data(self):
        """Test that set() updates the participant data"""
        participant = ParticipantFactory()
        session = ExperimentSessionFactory(participant=participant)

        proxy = ParticipantDataProxy(session)
        participant_data = proxy._get_db_object()

        # Set some data
        proxy.set({"favorite_color": "blue", "name": "New Name"})

        # Check that data was updated
        participant_data.refresh_from_db()
        assert participant_data.data == {"favorite_color": "blue", "name": "New Name"}

        # Check that participant name was updated
        participant.refresh_from_db()
        assert participant.name == "New Name"

    def test_get_schedules(self):
        """Test that get_schedules() returns scheduled messages for the participant"""
        participant = ParticipantFactory()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)
        proxy = ParticipantDataProxy(session)

        # Mock the get_schedules_for_experiment method on participant
        mock_schedules = [{"id": 1, "message": "Test reminder", "scheduled_time": "2023-01-01T10:00:00Z"}]
        with patch.object(
            Participant, "get_schedules_for_experiment", return_value=mock_schedules
        ) as mock_get_schedules:
            result = proxy.get_schedules()

            # Method should be called with the experiment and timezone params
            mock_get_schedules.assert_called_once_with(experiment, as_dict=True, as_timezone=proxy.get_timezone())
            assert result == mock_schedules

            # Subsequent calls should use cached result
            proxy.get_schedules()
            assert mock_get_schedules.call_count == 1

    def test_get_timezone(self):
        """Test that get_timezone returns the participant's timezone"""
        participant = ParticipantFactory()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)

        # Create participant data with a timezone
        participant_data = ParticipantData.objects.create(
            participant=participant, experiment=experiment, team=experiment.team, data={"timezone": "America/New_York"}
        )

        proxy = ParticipantDataProxy(session)
        proxy._participant_data = participant_data

        assert proxy.get_timezone() == "America/New_York"

        # Test with no timezone set
        participant_data.data = {}
        participant_data.save()
        assert proxy.get_timezone() is None
