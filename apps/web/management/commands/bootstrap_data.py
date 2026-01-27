"""
Management command to seed sample data for development and E2E tests.

Creates a test user, team, and sample data (chatbots, participants, files, etc.)
to enable developers to quickly set up a working environment.

Usage:
    python manage.py bootstrap_data                    # Use defaults
    python manage.py bootstrap_data --email dev@test.com --team-slug dev-team
    python manage.py bootstrap_data --skip-sample-data  # Only create user/team
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand

from apps.assistants.models import OpenAiAssistant
from apps.chat.models import ChatMessage, ChatMessageType
from apps.documents.models import Collection
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.files.models import File, FilePurpose
from apps.pipelines.models import Pipeline
from apps.service_providers.models import LlmProvider
from apps.service_providers.utils import get_first_llm_provider_model
from apps.teams import backends
from apps.teams.models import Membership, Team


class Command(BaseCommand):
    help = "Seeds the database with sample data for development and E2E testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default="test@example.com",
            help="Email for the test user (default: test@example.com)",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="testpassword",
            help="Password for the test user (default: testpassword)",
        )
        parser.add_argument(
            "--team-slug",
            type=str,
            default="test-team",
            help="Slug for the test team (default: test-team)",
        )
        parser.add_argument(
            "--team-name",
            type=str,
            default="Test Team",
            help="Name for the test team (default: Test Team)",
        )
        parser.add_argument(
            "--skip-sample-data",
            action="store_true",
            help="Only create user and team, skip sample data (chatbots, files, etc.)",
        )

    def handle(self, *args, **options):
        email = options["email"]
        password = options["password"]
        team_slug = options["team_slug"]
        team_name = options["team_name"]
        skip_sample_data = options["skip_sample_data"]

        self.stdout.write("=" * 50)
        self.stdout.write("Seeding development data...")
        self.stdout.write("=" * 50)

        # Create user and team
        user, team = self._create_user_and_team(email, password, team_slug, team_name)

        if not skip_sample_data:
            self._seed_sample_data(user, team)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Setup complete!"))
        self.stdout.write("")
        self.stdout.write("You can now log in with:")
        self.stdout.write(f"  Email: {email}")
        self.stdout.write(f"  Password: {password}")
        self.stdout.write(f"  Team: {team_slug}")
        self.stdout.write("")
        if skip_sample_data:
            self.stdout.write("To add sample data later, run:")
            self.stdout.write(f"  python manage.py bootstrap_data --email {email} --team-slug {team_slug}")

    def _create_user_and_team(self, email: str, password: str, team_slug: str, team_name: str):
        """Create test user and team with owner permissions."""
        User = get_user_model()

        # Initialize default groups
        try:
            backends.create_default_groups()
            self.stdout.write(self.style.SUCCESS("Default groups initialized"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not create default groups: {e}"))

        # Create or update user
        user, created = User.objects.get_or_create(username=email, defaults={"email": email})
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created user: {email}"))
        else:
            self.stdout.write(self.style.WARNING(f"User already exists: {email} (password updated)"))

        # Create or get team
        team, created = Team.objects.get_or_create(slug=team_slug, defaults={"name": team_name})

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created team: {team_slug}"))
        else:
            self.stdout.write(self.style.WARNING(f"Team already exists: {team_slug}"))

        # Ensure user is team owner
        membership = Membership.objects.filter(user=user, team=team).first()
        if not membership:
            backends.make_user_team_owner(team, user)
            self.stdout.write(self.style.SUCCESS("Added user to team as owner"))
        else:
            membership.groups.set(backends.get_team_owner_groups())
            self.stdout.write(self.style.WARNING("User already member of team (updated to owner)"))

        return user, team

    def _seed_sample_data(self, user, team):
        """Create sample pipelines, experiments, participants, files, etc."""

        # LLM Provider and Model
        self.stdout.write("")
        self.stdout.write("--- Creating LLM Provider ---")
        llm_provider, created = LlmProvider.objects.get_or_create(
            name="OpenAI", team=team, defaults={"type": "openai", "config": {"openai_api_key": "test-key"}}
        )
        self._log_created("LLM provider", llm_provider.name, created)

        llm_model = get_first_llm_provider_model(llm_provider, team.id)

        # Pipelines
        self.stdout.write("")
        self.stdout.write("--- Creating Pipelines ---")
        pipeline_names = [
            "Customer Support Pipeline",
            "Language Learning Pipeline",
            "Health Advisory Pipeline",
            "Programming Help Pipeline",
        ]

        pipelines = []
        for name in pipeline_names:
            pipeline = Pipeline.create_default(
                team=team, name=name, llm_provider_id=llm_provider.id, llm_provider_model=llm_model
            )
            self._log_created("pipeline", name, True)
            pipelines.append(pipeline)

        # Experiments (Chatbots)
        self.stdout.write("")
        self.stdout.write("--- Creating Chatbots (Experiments) ---")
        chatbot_names = [
            "Customer Support Bot",
            "Language Tutor",
            "Health Assistant",
            "Programming Helper",
        ]

        for i, name in enumerate(chatbot_names, 1):
            _experiment, created = Experiment.objects.get_or_create(
                team=team,
                name=name,
                defaults={
                    "owner": user,
                    "description": f"Test pipeline chatbot #{i} for development",
                    "pipeline": pipelines[i - 1] if i <= len(pipelines) else None,
                },
            )
            self._log_created("experiment", name, created)

        # Assistants
        self.stdout.write("")
        self.stdout.write("--- Creating Assistants ---")
        assistant_names = [
            "Code Review Assistant",
            "Documentation Writer",
            "Bug Triage Assistant",
        ]

        for i, name in enumerate(assistant_names, 1):
            assistant, created = OpenAiAssistant.objects.get_or_create(
                team=team,
                name=name,
                defaults={
                    "assistant_id": f"test_asst_{i}",
                    "instructions": f"You are a {name.lower()} that helps developers.",
                    "llm_provider": llm_provider,
                    "llm_provider_model": llm_model,
                    "temperature": 0.8,
                },
            )
            self._log_created("assistant", name, created)

        # Participants with Sessions
        self.stdout.write("")
        self.stdout.write("--- Creating Participants with Sessions ---")
        participant_data = [
            {"name": "Alice Johnson", "identifier": "alice.johnson@example.com", "platform": "web"},
            {"name": "Bob Smith", "identifier": "bob.smith@example.com", "platform": "web"},
            {"name": "Carol Williams", "identifier": "carol.williams@example.com", "platform": "api"},
            {"name": "David Brown", "identifier": "david.brown@example.com", "platform": "web"},
            {"name": "Eve Davis", "identifier": "eve.davis@example.com", "platform": "api"},
        ]

        first_experiment = Experiment.objects.filter(team=team).first()

        for data in participant_data:
            participant, created = Participant.objects.get_or_create(
                team=team,
                platform=data["platform"],
                identifier=data["identifier"],
                defaults={"name": data["name"]},
            )

            if created:
                self._log_created("participant", f"{data['name']} ({data['identifier']})", created)

                if first_experiment:
                    session = ExperimentSession.objects.create(
                        team=team, experiment=first_experiment, participant=participant
                    )
                    ChatMessage.objects.create(
                        chat=session.chat,
                        message_type=ChatMessageType.HUMAN,
                        content=f"Hello, I'm {data['name']}. Can you help me?",
                    )
                    ChatMessage.objects.create(
                        chat=session.chat,
                        message_type=ChatMessageType.AI,
                        content=f"Hello {data['name']}! I'd be happy to help. What do you need?",
                    )
                    ChatMessage.objects.create(
                        chat=session.chat,
                        message_type=ChatMessageType.HUMAN,
                        content="I have a question about your service.",
                    )
                    self.stdout.write(f"    Created session and messages for {data['name']}")
            else:
                self._log_created("participant", f"{data['name']}", created)

        # Files
        self.stdout.write("")
        self.stdout.write("--- Creating Files ---")
        file_data = [
            ("documentation.pdf", "application/pdf", b"Sample PDF content for testing"),
            ("data.csv", "text/csv", b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"),
            ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"Sample DOCX"),
            ("image.png", "image/png", b"\x89PNG\r\n\x1a\n..."),
        ]

        created_files = []
        for filename, content_type, content in file_data:
            uploaded_file = SimpleUploadedFile(filename, content, content_type=content_type)
            file_obj, created = File.objects.get_or_create(
                team=team,
                name=filename,
                defaults={
                    "file": uploaded_file,
                    "content_type": content_type,
                    "purpose": FilePurpose.COLLECTION,
                },
            )
            self._log_created("file", filename, created)
            created_files.append(file_obj)

        # Collection
        self.stdout.write("")
        self.stdout.write("--- Creating Collection ---")
        collection_name = "Sample Documents Collection"
        collection, created = Collection.objects.get_or_create(
            team=team,
            name=collection_name,
            defaults={"is_index": False},
        )

        if created:
            self._log_created("collection", collection_name, created)
            if created_files:
                collection.files.set(created_files)
                self.stdout.write(f"    Added {len(created_files)} file(s) to collection")
        else:
            self._log_created("collection", collection_name, created)
            if created_files:
                collection.files.add(*created_files)
                self.stdout.write(f"    Ensured {len(created_files)} file(s) in collection")

    def _log_created(self, entity_type: str, name: str, created: bool):
        """Helper to log entity creation status."""
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created {entity_type}: {name}"))
        else:
            self.stdout.write(self.style.WARNING(f"  {entity_type.capitalize()} already exists: {name}"))
