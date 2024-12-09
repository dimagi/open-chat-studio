from contextlib import contextmanager

from django.db import transaction

from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession
from apps.users.models import CustomUser


@contextmanager
def temporary_session(team):
    """A temporary sesssion setup that is rolled back after the context exits."""
    with transaction.atomic():
        owner = CustomUser.objects.create_user(username="test", password="test")
        consent_form = ConsentForm.objects.get(team=team, is_default=True)
        experiment = Experiment.objects.create(
            team=team, name="Temporary Experiment", owner=owner, consent_form=consent_form
        )
        chat = Chat.objects.create(team=team, name="Temporary Chat")
        experiment_session = ExperimentSession.objects.create(team=team, experiment=experiment, chat=chat)
        yield experiment_session
        transaction.set_rollback(True)
