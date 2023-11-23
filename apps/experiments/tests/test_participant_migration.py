from django.apps import apps
from django.test import TestCase

from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Participant, Prompt
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.teams_migration import migrate_participants


class ParticipantMigrationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create(email="user@test.com", username="user@test.com")
        cls.default_team = Team.objects.create(name="default_team", slug="default_team")
        cls.team2 = Team.objects.create(name="team2", slug="team2")
        cls.team3 = Team.objects.create(name="team3", slug="team3")

        cls.default_team_p1 = Participant.objects.create(team=cls.default_team, identifier="p1@test.com")
        cls.default_team_p2 = Participant.objects.create(team=cls.default_team, identifier="p2@test.com")
        cls.default_team_p3 = Participant.objects.create(team=cls.default_team, identifier="p3@test.com")
        cls.create_participant_and_session(cls.team2, "p2@test.com")
        cls.create_participant_and_session(cls.team3, "p2@test.com")
        cls.create_participant_and_session(cls.team3, "p3@test.com")
        cls.create_participant_and_session(cls.team3, "p4@test.com")

    @classmethod
    def create_participant_and_session(cls, team, identifier):
        participant = Participant.objects.create(team=team, identifier=identifier)

        exp = Experiment.objects.create(
            team=cls.default_team,
            owner=cls.user,
            name="default_team_experiment",
            chatbot_prompt=Prompt.objects.create(owner=cls.user, team=cls.default_team, prompt="prompt"),
            consent_form=ConsentForm.get_default(cls.default_team),
        )
        ExperimentSession.objects.create(
            team=cls.default_team,
            experiment=exp,
            participant=participant,
        )

    def test_migration(self):
        migrate_participants(apps, None)

        self.assertEqual(ExperimentSession.objects.filter(participant__team=self.default_team).count(), 4)
        self.assertEqual(ExperimentSession.objects.exclude(participant__team=self.default_team).count(), 0)

        self.assertEqual(Participant.objects.filter(team=self.default_team).count(), 4)
        self.assertEqual(Participant.objects.exclude(team=self.default_team).count(), 3)
