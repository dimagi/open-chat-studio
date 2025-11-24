import json

import pytest
from django.http import QueryDict

from apps.experiments.models import Participant
from apps.participants.filters import ParticipantFilter
from apps.utils.factories.experiment import ParticipantFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _get_querydict(params: dict) -> QueryDict:
    query_dict = QueryDict("", mutable=True)
    query_dict.update(params)
    return query_dict


@pytest.mark.django_db()
class TestParticipantFilter:
    @pytest.fixture()
    def participants_with_various_data(self):
        """Create participants with various identifier and name combinations"""
        p1 = ParticipantFactory(identifier="AP1", name="Alice Peterson")
        p2 = ParticipantFactory(identifier="AP2", name="Bob Anderson", team=p1.team)
        p3 = ParticipantFactory(identifier="AP3", name="Charlie Brown", team=p1.team)
        p4 = ParticipantFactory(identifier="XYZ123", name="AP Smith", team=p1.team)
        p5 = ParticipantFactory(identifier="TEST001", name="David Jones", team=p1.team)
        return [p1, p2, p3, p4, p5]

    def test_name_identifier_filter_contains_on_identifier(self, participants_with_various_data):
        """Test CONTAINS operator with OR logic - matches on identifier"""
        participants = participants_with_various_data
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "AP",
        }

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

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

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

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

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

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

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

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

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

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

        queryset = Participant.objects.filter(team=participants[0].team)
        participant_filter = ParticipantFilter()
        filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)), None)

        # Should match AP1 (identifier), XYZ123 (identifier), and David Jones (name)
        assert filtered.count() == 3
        assert set(filtered) == {participants[0], participants[3], participants[4]}
