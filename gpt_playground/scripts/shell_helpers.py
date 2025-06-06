from apps.assistants.models import OpenAiAssistant
from apps.experiments.models import Experiment, ExperimentSession

"""
These methods are meant to be imported by the shell for ease of use. Run like this:
PYTHONSTARTUP=./gpt_playground/scripts/shell_helpers.py python manage.py shell
"""


def get_experiment(id=None, public_id=None):
    if id:
        return Experiment.objects.get(id=id)

    if public_id:
        return Experiment.objects.get(public_id=public_id)

    raise ValueError("Either id or public_id must be provided")


def get_session(id=None, external_id=None):
    if id:
        return ExperimentSession.objects.get(id=id)

    if external_id:
        return ExperimentSession.objects.get(assistant_id=external_id)

    raise ValueError("Either id or external_id must be provided")


def get_assistant(id=None, assistant_id=None):
    if id:
        return OpenAiAssistant.objects.get(id=id)

    if assistant_id:
        return OpenAiAssistant.objects.get(assistant_id=assistant_id)

    raise ValueError("Either id or assistant_id must be provided")
