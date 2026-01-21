import os
from pathlib import Path

import environ
from django.core.management.base import BaseCommand

from apps.experiments.models import SyntheticVoice
from apps.service_providers.speech_service import AWSSpeechService, AzureSpeechService, OpenAISpeechService


class Command(BaseCommand):
    help = "Test speech services with real API calls"

    def add_arguments(self, parser):
        parser.add_argument(
            "--service", type=str, required=True, choices=["openai", "aws", "azure", "all"], help="Service to test"
        )
        parser.add_argument(
            "--text", type=str, default="This is a test of the speech synthesis service.", help="Text to synthesize"
        )
        parser.add_argument("--save-audio", type=str, help="Path to save synthesized audio (without extension)")
        parser.add_argument(
            "--env-file",
            type=str,
            default=".env.integration",
            help="Environment file to load (default: .env.integration)",
        )

    def handle(self, *args, **options):
        # Load environment variables using django-environ
        BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
        env = environ.Env()

        # Try to load specified env file, fall back to .env
        env_file = os.path.join(BASE_DIR, options["env_file"])
        if os.path.exists(env_file):
            env.read_env(env_file)
            self.stdout.write(f"Loaded environment from: {env_file}")
        else:
            env.read_env(os.path.join(BASE_DIR, ".env"))
            self.stdout.write(self.style.WARNING(f"File {env_file} not found, using .env"))

        service_type = options["service"]
        text = options["text"]
        save_path = options.get("save_audio")

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("Testing Speech Services with Real APIs")
        self.stdout.write("=" * 50 + "\n")

        if service_type in ["openai", "all"]:
            self._test_openai(env, text, save_path)

        if service_type in ["aws", "all"]:
            self._test_aws(env, text, save_path)

        if service_type in ["azure", "all"]:
            self._test_azure(env, text, save_path)

        self.stdout.write("\n" + "=" * 50)

    def _test_openai(self, env, text, save_path):
        api_key = env.str("OPENAI_API_KEY", default=None)
        if not api_key:
            self.stdout.write(self.style.WARNING("⊗ OpenAI: OPENAI_API_KEY not set"))
            return

        try:
            service = OpenAISpeechService(
                openai_api_key=api_key,
                openai_api_base=env.str("OPENAI_API_BASE", default=None),
                openai_organization=env.str("OPENAI_ORGANIZATION", default=None),
            )

            voice = SyntheticVoice(name="alloy", service="OpenAI")
            result = service.synthesize_voice(text, voice)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ OpenAI: Synthesized {result.duration:.2f}s "
                    f"({len(result.audio.getvalue())} bytes, {result.format})"
                )
            )

            if save_path:
                path = f"{save_path}_openai.{result.format}"
                with open(path, "wb") as f:
                    f.write(result.audio.getvalue())
                self.stdout.write(f"  Saved to: {path}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ OpenAI: {e}"))

    def _test_aws(self, env, text, save_path):
        access_key = env.str("AWS_ACCESS_KEY_ID", default=None)
        secret_key = env.str("AWS_SECRET_ACCESS_KEY", default=None)

        if not (access_key and secret_key):
            self.stdout.write(self.style.WARNING("⊗ AWS: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY not set"))
            return

        try:
            service = AWSSpeechService(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_region=env.str("AWS_REGION", default="us-east-1"),
            )

            voice = SyntheticVoice(name="Joanna", service="AWS", neural=True)
            result = service.synthesize_voice(text, voice)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ AWS: Synthesized {result.duration:.2f}s ({len(result.audio.getvalue())} bytes, {result.format})"
                )
            )

            if save_path:
                path = f"{save_path}_aws.{result.format}"
                with open(path, "wb") as f:
                    f.write(result.audio.getvalue())
                self.stdout.write(f"  Saved to: {path}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ AWS: {e}"))

    def _test_azure(self, env, text, save_path):
        subscription_key = env.str("AZURE_SPEECH_KEY", default=None)
        if not subscription_key:
            self.stdout.write(self.style.WARNING("⊗ Azure: AZURE_SPEECH_KEY not set"))
            return

        try:
            service = AzureSpeechService(
                azure_subscription_key=subscription_key,
                azure_region=env.str("AZURE_SPEECH_REGION", default="eastus"),
            )

            voice = SyntheticVoice(name="JennyNeural", service="Azure", language_code="en-US")
            result = service.synthesize_voice(text, voice)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Azure: Synthesized {result.duration:.2f}s "
                    f"({len(result.audio.getvalue())} bytes, {result.format})"
                )
            )

            if save_path:
                path = f"{save_path}_azure.{result.format}"
                with open(path, "wb") as f:
                    f.write(result.audio.getvalue())
                self.stdout.write(f"  Saved to: {path}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Azure: {e}"))
