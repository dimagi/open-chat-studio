import pytest
from django.utils import timezone

from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


@pytest.fixture()
def participant_data(team_with_users):
    experiment = ExperimentFactory.create(team=team_with_users)
    participant = ParticipantFactory.create(team=team_with_users)
    return ParticipantData.objects.create(team=team_with_users, participant=participant, experiment=experiment)


@pytest.fixture()
def form_id(participant_data):
    return participant_data.experiment.consent_form_id


@pytest.mark.django_db()
def test_record_consent_marks_the_participant_as_consented(participant_data, form_id):
    assert not participant_data.has_consented()

    participant_data.record_consent(form_id)

    participant_data.refresh_from_db()
    assert participant_data.has_consented()


@pytest.mark.django_db()
def test_record_consent_remembers_which_form_was_accepted(participant_data, form_id):
    assert not participant_data.has_consented_to(form_id)

    participant_data.record_consent(form_id)

    participant_data.refresh_from_db()
    assert participant_data.has_consented_to(form_id)
    assert not participant_data.has_consented_to(form_id + 1)


@pytest.mark.django_db()
def test_consent_recorded_without_a_form_does_not_cover_any_form(participant_data, form_id):
    participant_data.update_consent(True)

    assert participant_data.has_consented()
    assert not participant_data.has_consented_to(form_id)


@pytest.mark.django_db()
def test_record_consent_stamps_the_time_of_acceptance(participant_data, form_id):
    before = timezone.now()

    participant_data.record_consent(form_id)

    participant_data.refresh_from_db()
    consent_at = timezone.datetime.fromisoformat(participant_data.system_metadata["consent_at"])
    assert before <= consent_at <= timezone.now()


@pytest.mark.django_db()
def test_record_consent_keeps_other_system_metadata(participant_data, form_id):
    participant_data.system_metadata = {"commcare_connect_channel_id": "abc"}
    participant_data.save()

    participant_data.record_consent(form_id)

    participant_data.refresh_from_db()
    assert participant_data.system_metadata["commcare_connect_channel_id"] == "abc"
