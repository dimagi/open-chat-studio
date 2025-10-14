from unittest.mock import patch

import pytest

from apps.experiments.models import Participant
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
class TestParticipantDataProxy:
    """Tests for the ParticipantDataProxy class that handles participant data access"""

    def test_initialization(self):
        """Test that ParticipantDataProxy initializes correctly"""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment)
        proxy = ParticipantDataProxy({}, session)

        assert proxy.session == session
        assert proxy.experiment == experiment
        assert proxy._participant_data == {}
        assert proxy._scheduled_messages is None

    def test_from_state(self):
        """Test creating a ParticipantDataProxy from pipeline state"""
        session = ExperimentSessionFactory()
        pipeline_state = {"experiment_session": session, "participant_data": {"test": 1}}
        proxy = ParticipantDataProxy.from_state(pipeline_state)

        assert proxy.session == session
        assert proxy.experiment == session.experiment
        assert proxy._participant_data == {"test": 1}

    def test_from_state_with_missing_session(self):
        """Test handling when state doesn't have experiment_session"""
        pipeline_state = {}
        proxy = ParticipantDataProxy.from_state(pipeline_state)

        assert proxy.session is None
        assert proxy.experiment is None

    def test_get_returns_merged_data(self):
        """Test that get() merges participant global data with participant data"""
        participant = ParticipantFactory(name="Test User")
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)

        proxy = ParticipantDataProxy({"participant_data": {"favorite_color": "blue"}}, session)

        # Participant's global_data should include the name
        expected_data = {"name": "Test User", "favorite_color": "blue"}
        assert proxy.get() == expected_data

    def test_set_validates_data_type(self):
        """Test that set() validates the data type"""
        session = ExperimentSessionFactory()
        proxy = ParticipantDataProxy({}, session)

        with pytest.raises(ValueError, match="Data must be a dictionary"):
            proxy.set("not a dictionary")

    def test_set_updates_participant_data(self):
        """Test that set() updates the participant data"""
        session = ExperimentSessionFactory()

        input_state = {}
        proxy = ParticipantDataProxy(input_state, session)

        # Set some data
        proxy.set({"favorite_color": "blue", "name": "New Name"})

        # Check that data was updated
        assert input_state["participant_data"] == {"favorite_color": "blue", "name": "New Name"}

    def test_get_schedules(self):
        """Test that get_schedules() returns scheduled messages for the participant"""
        participant = ParticipantFactory()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, participant=participant)
        proxy = ParticipantDataProxy({}, session)

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
        session = ExperimentSessionFactory()

        proxy = ParticipantDataProxy({"participant_data": {"timezone": "America/New_York"}}, session)

        assert proxy.get_timezone() == "America/New_York"

        # Test with no timezone set
        proxy = ParticipantDataProxy({}, session)
        assert proxy.get_timezone() is None

    def test_set_key(self):
        """Test that set_key() updates a single key in the participant data."""
        session = ExperimentSessionFactory()

        input_state = {"participant_data": {"name": "jack"}}
        proxy = ParticipantDataProxy(input_state, session)

        # Set some data
        proxy.set_key("favorite_color", "blue")

        # Check that data was updated
        assert input_state["participant_data"] == {"name": "jack", "favorite_color": "blue"}

    def test_append_to_key(self):
        """
        Test that append_to_key() adds a value to a list at the specified key.
        If the current value is not a list, it should convert it to a list.
        """
        session = ExperimentSessionFactory()

        input_state = {}
        proxy = ParticipantDataProxy(input_state, session)

        assert "random_stuff" not in proxy.get()

        # Append some data
        proxy.append_to_key("random_stuff", "blue")
        proxy.append_to_key("random_stuff", 1)
        proxy.append_to_key("random_stuff", [3, 4])

        # Check that data was updated
        assert input_state["participant_data"] == {"random_stuff": ["blue", 1, 3, 4]}

    def test_increment_key(self):
        """
        Test that increment_key() increments a numeric value at the specified key.
        If the current value is not a number, it should initialize to 0 before incrementing.
        """
        session = ExperimentSessionFactory()

        input_state = {}
        proxy = ParticipantDataProxy(input_state, session)

        # Test incrementing a non-existent key (should start at 0)
        proxy.increment_key("counter")
        assert input_state["participant_data"] == {"counter": 1}

        # Test incrementing an existing numeric value
        proxy.increment_key("counter", 5)
        assert input_state["participant_data"] == {"counter": 6}

        # Test incrementing with default increment of 1
        proxy.increment_key("counter")
        assert input_state["participant_data"] == {"counter": 7}

        # Test incrementing a float value
        proxy.set_key("float_counter", 2.5)
        proxy.increment_key("float_counter", 1.5)
        assert input_state["participant_data"] == {"counter": 7, "float_counter": 4.0}

        # Test incrementing a non-numeric value (should reset to 0 and increment)
        proxy.set_key("text_value", "not a number")
        proxy.increment_key("text_value", 3)
        assert input_state["participant_data"] == {"counter": 7, "float_counter": 4.0, "text_value": 3}

    def test_get_participant_identifier(self):
        """Test that get_participant_identifier() returns the participant's identifier"""
        participant = ParticipantFactory(identifier="test_user@example.com")
        session = ExperimentSessionFactory(participant=participant)

        proxy = ParticipantDataProxy({}, session)

        assert proxy.get_participant_identifier() == "test_user@example.com"

    def test_get_participant_identifier_no_session(self):
        """Test that get_participant_identifier() returns None when there's no session"""
        proxy = ParticipantDataProxy({}, None)

        assert proxy.get_participant_identifier() is None

    def test_get_participant_identifier_no_participant(self):
        """Test that get_participant_identifier() returns None when there's no participant"""
        session = ExperimentSessionFactory(participant=None)

        proxy = ParticipantDataProxy({}, session)

        assert proxy.get_participant_identifier() is None
