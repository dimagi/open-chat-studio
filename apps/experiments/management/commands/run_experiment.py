from django.core.management.base import BaseCommand

from apps.chat.bots import get_bot_from_experiment
from apps.chat.models import Chat
from apps.experiments.models import Experiment


class Command(BaseCommand):
    help = "Loads data from a directory"

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", type=int)
        parser.add_argument("--resume", type=int, default=None, help="Chat ID to resume.")

    def handle(self, *args, **options):
        experiment = Experiment.objects.get(id=options["experiment_id"])
        chat = None
        if options["resume"]:
            chat = Chat.objects.get(id=options["resume"])
        else:
            chat = Chat.objects.create(team=experiment.team)
        print(chat)
        explainer_bot = get_bot_from_experiment(experiment, chat)
        run_explainer_bot(explainer_bot, experiment)


def run_explainer_bot(explainer_bot, experiment):
    user_input = input(f"Please enter your question about {experiment.name} here:\n\n")
    while True:
        if user_input.lower() == "exit":
            break

        result = explainer_bot.process_input(user_input)
        user_input = input(f"\n{result}\n\n")
