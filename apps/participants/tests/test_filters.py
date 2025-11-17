import json

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import QueryDict
from django.test import RequestFactory
from time_machine import travel

from apps.experiments.models import Participant
from apps.participants.filters import ParticipantFilter
from apps.utils.factories.experiment import ParticipantFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _get_querydict(params: dict) -> QueryDict:
    query_dict = QueryDict("", mutable=True)
    query_dict.update(params)
    return query_dict


def attach_session_middleware_to_request(request):
    session_middleware = SessionMiddleware(lambda req: None)
    session_middleware.process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


@pytest.mark.django_db()
class TestParticipantFilter:
    @pytest.fixture()
    def base_participant(self):
        """Create a base participant"""
        return ParticipantFactory(
            identifier="test-participant-001",
            name="John Doe",
        )

    @pytest.fixture()
    def participants_with_various_data(self):
        """Create participants with various identifier and name combinations"""
        p1 = ParticipantFactory(identifier="AP1", name="Alice Peterson")
        p2 = ParticipantFactory(identifier="AP2", name="Bob Anderson", team=p1.team)
        p3 = ParticipantFactory(identifier="AP3", name="Charlie Brown", team=p1.team)
        p4 = ParticipantFactory(identifier="XYZ123", name="AP Smith", team=p1.team)
        p5 = ParticipantFactory(identifier="TEST001", name="David Jones", team=p1.team)
        return [p1, p2, p3, p4, p5]

    @pytest.fixture()
    def participants_with_platforms(self):
        """Create participants with different platforms"""
        p1 = ParticipantFactory(identifier="P1", name="Web User", platform="web")
        p2 = ParticipantFactory(identifier="P2", name="WhatsApp User", platform="whatsapp", team=p1.team)
        p3 = ParticipantFactory(identifier="P3", name="Telegram User", platform="telegram", team=p1.team)
        p4 = ParticipantFactory(identifier="P4", name="SMS User", platform="sms", team=p1.team)
        return [p1, p2, p3, p4]

    def test_name_identifier_filter_equals_on_identifier(self, base_participant):
        """Test EQUALS operator matches on identifier"""
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "test-participant-001",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=base_participant.team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        assert filtered.count() == 1
        assert filtered.first() == base_participant

    def test_name_identifier_filter_equals_on_name(self, base_participant):
        """Test EQUALS operator matches on name"""
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "John Doe",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=base_participant.team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        assert filtered.count() == 1
        assert filtered.first() == base_participant

    def test_name_identifier_filter_contains_on_identifier(self, participants_with_various_data):
        """Test CONTAINS operator with OR logic - matches on identifier"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "AP",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should match AP1, AP2, AP3 (identifier contains "AP") AND "AP Smith" (name contains "AP")
        assert filtered.count() == 4
        assert set(filtered) == {participants[0], participants[1], participants[2], participants[3]}

    def test_name_identifier_filter_contains_on_name(self, participants_with_various_data):
        """Test CONTAINS operator with OR logic - matches on name"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "Anderson",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should match "Bob Anderson"
        assert filtered.count() == 1
        assert filtered.first() == participants[1]

    def test_name_identifier_filter_does_not_contain(self, participants_with_various_data):
        """Test DOES_NOT_CONTAIN operator with OR logic"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.DOES_NOT_CONTAIN,
            "filter_0_value": "AP",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should exclude anyone with "AP" in identifier OR name
        # This excludes AP1, AP2, AP3, and "AP Smith"
        # Only TEST001/David Jones remains
        assert filtered.count() == 1
        assert filtered.first() == participants[4]

    def test_name_identifier_filter_starts_with(self, participants_with_various_data):
        """Test STARTS_WITH operator with OR logic"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.STARTS_WITH,
            "filter_0_value": "AP",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should match AP1, AP2, AP3 (identifier starts with "AP") AND "AP Smith" (name starts with "AP")
        assert filtered.count() == 4
        assert set(filtered) == {participants[0], participants[1], participants[2], participants[3]}

    def test_name_identifier_filter_ends_with(self, participants_with_various_data):
        """Test ENDS_WITH operator with OR logic"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.ENDS_WITH,
            "filter_0_value": "Smith",
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should match "AP Smith" (name ends with "Smith")
        assert filtered.count() == 1
        assert filtered.first() == participants[3]

    def test_name_identifier_filter_any_of(self, participants_with_various_data):
        """Test ANY_OF operator with OR logic"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["AP1", "David Jones", "XYZ123"]),
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should match AP1 (identifier), XYZ123 (identifier), and David Jones (name)
        assert filtered.count() == 3
        assert set(filtered) == {participants[0], participants[3], participants[4]}

    @travel("2025-01-15 10:00:00", tick=False)
    def test_created_on_timestamp_filters(self):
        """Test Created On timestamp filtering"""
        # Create participants on different dates
        with travel("2025-01-10 10:00:00", tick=False):
            p1 = ParticipantFactory(identifier="P1", name="User One")

        with travel("2025-01-12 10:00:00", tick=False):
            p2 = ParticipantFactory(identifier="P2", name="User Two", team=p1.team)

        with travel("2025-01-14 10:00:00", tick=False):
            p3 = ParticipantFactory(identifier="P3", name="User Three", team=p1.team)

        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=p1.team)

        # Test ON
        params = {
            "filter_0_column": "created_on",
            "filter_0_operator": Operators.ON,
            "filter_0_value": "2025-01-12",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        assert filtered.count() == 1
        assert filtered.first() == p2

        # Test BEFORE
        params = {
            "filter_0_column": "created_on",
            "filter_0_operator": Operators.BEFORE,
            "filter_0_value": "2025-01-12",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        assert filtered.count() == 1
        assert filtered.first() == p1

        # Test AFTER
        params = {
            "filter_0_column": "created_on",
            "filter_0_operator": Operators.AFTER,
            "filter_0_value": "2025-01-12",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        assert filtered.count() == 1
        assert filtered.first() == p3

        # Test RANGE (last 7 days from Jan 15)
        params = {
            "filter_0_column": "created_on",
            "filter_0_operator": Operators.RANGE,
            "filter_0_value": "3d",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        # Should include p2 (Jan 12) and p3 (Jan 14), but not p1 (Jan 10)
        assert filtered.count() == 2
        assert set(filtered) == {p2, p3}

    def test_platform_filter(self, participants_with_platforms):
        """Test Channels/Platform filter"""
        participants = participants_with_platforms

        # Test ANY_OF with single platform
        params = {
            "filter_0_column": "channels",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["Web"]),
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        assert filtered.count() == 1

        # Test ANY_OF with multiple platforms
        params["filter_0_value"] = json.dumps(["WhatsApp", "Telegram"])
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        assert filtered.count() == 2
        assert set(filtered) == {participants[1], participants[2]}

        # Test EXCLUDES
        params["filter_0_operator"] = Operators.EXCLUDES
        params["filter_0_value"] = json.dumps(["Web"])
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        assert filtered.count() == 3
        assert set(filtered) == {participants[1], participants[2], participants[3]}

    def test_multiple_filters_combined(self, participants_with_various_data):
        """Test combining multiple filters"""
        participants = participants_with_various_data
        # Set remote IDs for some participants
        participants[0].remote_id = "remote-123"
        participants[0].save()
        participants[1].remote_id = "remote-456"
        participants[1].save()

        # Filter: Name/Identifier contains "AP" AND Remote ID is "remote-123"
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "AP",
            "filter_1_column": "remote_id",
            "filter_1_operator": Operators.ANY_OF,
            "filter_1_value": json.dumps(["remote-123"]),
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should only match AP1 (has "AP" in identifier and remote-123)
        assert filtered.count() == 1
        assert filtered.first() == participants[0]

    def test_empty_filters(self, base_participant):
        """Test behavior with empty or invalid filters"""
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=base_participant.team)
        initial_count = queryset.count()

        # Empty filter values should be ignored
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        assert filtered.count() == initial_count

        # Invalid filter column should be ignored
        params = {
            "filter_0_column": "invalid_column",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "test",
        }
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)
        assert filtered.count() == initial_count

    def test_name_identifier_filter_case_insensitive(self, participants_with_various_data):
        """Test that Name/Identifier filter is case-insensitive"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "ap",  # lowercase
        }
        factory = RequestFactory()
        request = factory.get("/")
        attach_session_middleware_to_request(request)
        timezone = request.session.get("detected_tz", None)

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), timezone)

        # Should still match AP1, AP2, AP3, and "AP Smith" despite lowercase search
        assert filtered.count() == 4
        assert set(filtered) == {participants[0], participants[1], participants[2], participants[3]}
