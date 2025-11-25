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


@pytest.fixture(scope="class")
def participants_with_various_data(django_db_setup, django_db_blocker):
    """Create participants with various identifier and name combinations"""
    with django_db_blocker.unblock():
        p1 = ParticipantFactory(identifier="AP1", name="Alice Peterson")
        p2 = ParticipantFactory(identifier="AP2", name="Bob Anderson", team=p1.team)
        p3 = ParticipantFactory(identifier="AP3", name="Charlie Brown", team=p1.team)
        p4 = ParticipantFactory(identifier="XYZ123", name="AP Smith", team=p1.team)
        p5 = ParticipantFactory(identifier="TEST001", name="David Jones", team=p1.team)
        participants = [p1, p2, p3, p4, p5]
        yield participants
        Participant.objects.filter(id__in=[p.id for p in participants]).delete()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("operator", "value", "expected_identifiers", "test_description"),
    [
        (Operators.CONTAINS, "AP", {"AP1", "AP2", "AP3", "XYZ123"}),
        (Operators.CONTAINS, "Anderson", {"AP2"}),
        (Operators.DOES_NOT_CONTAIN, "AP", {"TEST001"}),
        (Operators.STARTS_WITH, "AP", {"AP1", "AP2", "AP3", "XYZ123"}),
        (Operators.ENDS_WITH, "Smith", {"XYZ123"}),
        (Operators.ANY_OF, json.dumps(["AP1", "David Jones", "XYZ123"]), {"AP1", "XYZ123", "TEST001"}),
    ],
)
def test_name_identifier_filter(participants_with_various_data, operator, value, expected_identifiers):
    """Test participant filter with various operators using OR logic on identifier and name"""
    participants = participants_with_various_data
    params = {
        "filter_0_column": "participant",
        "filter_0_operator": operator,
        "filter_0_value": value,
    }

    queryset = Participant.objects.filter(team=participants[0].team)
    participant_filter = ParticipantFilter()
    filtered = participant_filter.apply(queryset, FilterParams(_get_querydict(params)))

    assert {p.identifier for p in filtered} == expected_identifiers
