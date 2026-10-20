import pytest
from django.db import IntegrityError

from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.utils.factories.moderation import DeniedParticipantFactory


@pytest.mark.django_db()
class TestDeniedParticipant:
    def test_unique_per_team_and_participant(self):
        denial = DeniedParticipantFactory.create()

        with pytest.raises(IntegrityError):
            DeniedParticipant.objects.create(
                team=denial.team, participant=denial.participant, source=DeniedParticipantSource.MANUAL
            )

    def test_deleted_with_the_participant(self):
        denial = DeniedParticipantFactory.create()

        denial.participant.delete()

        assert not DeniedParticipant.objects.filter(id=denial.id).exists()
