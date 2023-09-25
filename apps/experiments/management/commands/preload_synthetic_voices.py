import json
import os

from django.core.management.base import BaseCommand

from apps.experiments.models import SyntheticVoice


class Command(BaseCommand):
    # AWS voices: https://docs.aws.amazon.com/polly/latest/dg/voicelist.html
    help = "Populates Synthetic voices"

    def handle(self, *args, **options):
        voice_data = {}
        current_directory = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_directory, "aws_voices.json")

        with open(file_path, "r") as json_file:
            voice_data = json.load(json_file)["voices"]

        voices_created = 0
        for voice in voice_data:
            _, created = SyntheticVoice.objects.get_or_create(
                name=voice["name"],
                language=voice["language"],
                gender=voice["gender"],
                neural=voice["neural"],
            )

            if created:
                voices_created += 1
        print(f"{voices_created} synthetic voices were created")
