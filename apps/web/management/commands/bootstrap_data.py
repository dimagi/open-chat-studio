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

from apps.annotations.models import Tag
from apps.chat.models import ChatMessage, ChatMessageType
from apps.documents.models import Collection
from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMode,
    Evaluator,
)
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.files.models import File, FilePurpose
from apps.pipelines.models import Pipeline
from apps.service_providers.llm_service.credentials import (
    ProviderCredentials,
    get_provider_credentials_from_env,
)
from apps.service_providers.models import LlmProvider, LlmProviderTypes
from apps.service_providers.utils import get_first_llm_provider_model
from apps.teams import backends
from apps.teams.models import Membership, Team
from apps.trace.models import Trace, TraceStatus

_PIPELINE_NAMES = [
    "Customer Support Pipeline",
    "Language Learning Pipeline",
    "Health Advisory Pipeline",
    "Programming Help Pipeline",
]
_CHATBOT_NAMES = [
    "Customer Support Bot",
    "Language Tutor",
    "Health Assistant",
    "Programming Helper",
]
_TAG_NAMES = ["urgent", "resolved", "needs-review", "feedback"]
_PARTICIPANT_DATA = [
    {"name": "Alice Johnson", "identifier": "alice.johnson@example.com", "platform": "web"},
    {"name": "Bob Smith", "identifier": "bob.smith@example.com", "platform": "web"},
    {"name": "Carol Williams", "identifier": "carol.williams@example.com", "platform": "api"},
    {"name": "David Brown", "identifier": "david.brown@example.com", "platform": "web"},
    {"name": "Eve Davis", "identifier": "eve.davis@example.com", "platform": "api"},
]
_FILE_DATA = [
    ("documentation.pdf", "application/pdf", b"Sample PDF content for testing"),
    ("data.csv", "text/csv", b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"),
    ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"Sample DOCX"),
    ("image.png", "image/png", b"\x89PNG\r\n\x1a\n..."),
]
_EVALUATION_DATASET_MESSAGES = [
    ("My order arrived broken!", "I'm so sorry — let me get a replacement out today."),
    ("Thanks for the fast shipping!", "Glad to hear it — enjoy!"),
    ("This product doesn't match the description.", "I understand, let me help you with a return."),
]


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
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Make the test user a superuser (default: False)",
        )

    def handle(self, *args, **options):
        email = options["email"]
        password = options["password"]
        team_slug = options["team_slug"]
        team_name = options["team_name"]
        skip_sample_data = options["skip_sample_data"]
        superuser = options["superuser"]

        self.stdout.write("=" * 50)
        self.stdout.write("Seeding development data...")
        self.stdout.write("=" * 50)

        # Create user and team
        user, team = self._create_user_and_team(email, password, team_slug, team_name, superuser)

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

    def _create_user_and_team(self, email: str, password: str, team_slug: str, team_name: str, superuser: bool = False):
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
        if superuser:
            user.is_staff = True
            user.is_superuser = True
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created user: {email}"))
        else:
            self.stdout.write(self.style.WARNING(f"User already exists: {email} (password updated)"))

        if superuser:
            self.stdout.write(self.style.SUCCESS("  User granted superuser privileges"))

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
        """Orchestrate creation of sample pipelines, experiments, tags, sessions, traces, etc."""
        llm_provider, llm_model = self._seed_llm_providers(team)
        pipelines = self._seed_pipelines(team, llm_provider, llm_model)
        experiments = self._seed_experiments(user, team, pipelines)
        tags = self._seed_tags(user, team)
        sessions = self._seed_participants_and_sessions(user, team, experiments, tags)
        self._seed_traces(team, sessions)
        files = self._seed_files(team)
        self._seed_collection(team, files)
        self._seed_evaluation(team, llm_provider, llm_model)

    def _seed_llm_providers(self, team):
        self.stdout.write("")
        self.stdout.write("--- Creating LLM Providers ---")
        provider_credentials = get_provider_credentials_from_env()
        if not provider_credentials:
            self.stdout.write(
                self.style.WARNING(
                    "  No provider env vars set; creating a placeholder OpenAI provider with a dummy key."
                    " Set OPENAI_API_KEY (or another supported key — see .env.example) to enable real LLM calls."
                )
            )
            provider_credentials = [
                ProviderCredentials(
                    type=LlmProviderTypes.openai,
                    name="OpenAI",
                    config={"openai_api_key": "test-key"},
                )
            ]

        llm_providers = [self._get_or_create_llm_provider(team, creds) for creds in provider_credentials]
        llm_provider = llm_providers[0]
        return llm_provider, get_first_llm_provider_model(llm_provider, team.id)

    def _get_or_create_llm_provider(self, team, creds: ProviderCredentials) -> LlmProvider:
        # LlmProvider has no unique constraint on (team, type), so a manually-created
        # provider of the same type would make get_or_create raise MultipleObjectsReturned.
        provider = LlmProvider.objects.filter(team=team, type=str(creds.type)).first()
        if provider is None:
            provider = LlmProvider.objects.create(team=team, type=str(creds.type), name=creds.name, config=creds.config)
            self._log_created("LLM provider", f"{provider.name} ({creds.type})", True)
        else:
            self._log_created("LLM provider", f"{provider.name} ({creds.type})", False)
        return provider

    def _seed_pipelines(self, team, llm_provider, llm_model):
        self.stdout.write("")
        self.stdout.write("--- Creating Pipelines ---")
        pipelines = []
        for name in _PIPELINE_NAMES:
            pipeline = Pipeline.create_default(
                team=team, name=name, llm_provider_id=llm_provider.id, llm_provider_model=llm_model
            )
            self._log_created("pipeline", name, True)
            pipelines.append(pipeline)
        return pipelines

    def _seed_experiments(self, user, team, pipelines):
        self.stdout.write("")
        self.stdout.write("--- Creating Chatbots (Experiments) ---")
        experiments = []
        for i, (name, pipeline) in enumerate(zip(_CHATBOT_NAMES, pipelines, strict=True), 1):
            experiment, created = Experiment.objects.get_or_create(
                team=team,
                name=name,
                defaults={
                    "owner": user,
                    "description": f"Test pipeline chatbot #{i} for development",
                    "pipeline": pipeline,
                },
            )
            self._log_created("experiment", name, created)
            experiments.append(experiment)
        return experiments

    def _seed_tags(self, user, team):
        self.stdout.write("")
        self.stdout.write("--- Creating Tags ---")
        tags = []
        for tag_name in _TAG_NAMES:
            tag, created = Tag.objects.get_or_create(
                team=team,
                name=tag_name,
                is_system_tag=False,
                category="",
                defaults={"created_by": user},
            )
            self._log_created("tag", tag_name, created)
            tags.append(tag)
        return tags

    def _seed_participants_and_sessions(self, user, team, experiments, tags) -> list[ExperimentSession]:
        self.stdout.write("")
        self.stdout.write("--- Creating Participants with Sessions ---")
        seeded_sessions = []
        for idx, data in enumerate(_PARTICIPANT_DATA):
            participant, created = Participant.objects.get_or_create(
                team=team,
                platform=data["platform"],
                identifier=data["identifier"],
                defaults={"name": data["name"]},
            )
            if not created:
                self._log_created("participant", data["name"], created)
                continue

            self._log_created("participant", f"{data['name']} ({data['identifier']})", created)
            target_experiment = experiments[idx % len(experiments)]
            tag = tags[idx % len(tags)]
            session = self._create_session_with_messages(user, team, target_experiment, participant, data, tag)
            seeded_sessions.append(session)
            self.stdout.write(f"    Session for {data['name']} → {target_experiment.name} (tag: {tag.name})")
        return seeded_sessions

    def _create_session_with_messages(self, user, team, experiment, participant, data, tag) -> ExperimentSession:
        session = ExperimentSession.objects.create(team=team, experiment=experiment, participant=participant)
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
        session.chat.add_tag(tag, team=team, added_by=user)
        return session

    def _seed_traces(self, team, sessions: list[ExperimentSession]) -> None:
        if not sessions:
            return
        self.stdout.write("")
        self.stdout.write("--- Creating Traces ---")
        for session in sessions:
            messages = list(session.chat.messages.order_by("created_at"))
            input_msg = next((m for m in messages if m.message_type == ChatMessageType.HUMAN), None)
            output_msg = next((m for m in messages if m.message_type == ChatMessageType.AI), None)
            Trace.objects.create(
                team=team,
                experiment=session.experiment,
                session=session,
                participant=session.participant,
                input_message=input_msg,
                output_message=output_msg,
                status=TraceStatus.SUCCESS,
                duration=1234,
                n_turns=1,
                n_toolcalls=0,
                n_total_tokens=250,
                n_prompt_tokens=200,
                n_completion_tokens=50,
            )
        self.stdout.write(self.style.SUCCESS(f"  Created {len(sessions)} trace(s)"))

    def _seed_files(self, team) -> list[File]:
        self.stdout.write("")
        self.stdout.write("--- Creating Files ---")
        created_files = []
        for filename, content_type, content in _FILE_DATA:
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
        return created_files

    def _seed_collection(self, team, files: list[File]) -> None:
        self.stdout.write("")
        self.stdout.write("--- Creating Collection ---")
        collection_name = "Sample Documents Collection"
        collection, created = Collection.objects.get_or_create(
            team=team,
            name=collection_name,
            defaults={"is_index": False},
        )
        self._log_created("collection", collection_name, created)
        if not files:
            return
        if created:
            collection.files.set(files)
            self.stdout.write(f"    Added {len(files)} file(s) to collection")
        else:
            collection.files.add(*files)
            self.stdout.write(f"    Ensured {len(files)} file(s) in collection")

    def _seed_evaluation(self, team, llm_provider, llm_model) -> None:
        self.stdout.write("")
        self.stdout.write("--- Creating Evaluation ---")
        evaluator = self._seed_evaluator(team, llm_provider, llm_model)
        dataset = self._seed_evaluation_dataset(team)
        config, created = EvaluationConfig.objects.get_or_create(
            team=team,
            name="Sentiment Analysis Run",
            defaults={"dataset": dataset},
        )
        if created:
            config.evaluators.add(evaluator)
        self._log_created("evaluation config", config.name, created)

    def _seed_evaluator(self, team, llm_provider, llm_model) -> Evaluator:
        params = {
            "llm_provider_id": llm_provider.id,
            "llm_temperature": 0.3,
            "prompt": (
                "Analyse the sentiment of the user message.\n\nUser: {input.content}\nAssistant: {output.content}"
            ),
            "output_schema": {
                "sentiment": {
                    "type": "choice",
                    "description": "Detected sentiment of the user's message",
                    "choices": ["positive", "neutral", "negative"],
                    "use_in_aggregations": True,
                },
                "score": {
                    "type": "int",
                    "description": "Sentiment score from 1 (very negative) to 10 (very positive)",
                    "ge": 1,
                    "le": 10,
                    "use_in_aggregations": True,
                },
            },
        }
        if llm_model:
            params["llm_provider_model_id"] = llm_model.id

        evaluator, created = Evaluator.objects.get_or_create(
            team=team,
            name="Sentiment Analyzer",
            defaults={
                "type": "LlmEvaluator",
                "evaluation_mode": EvaluationMode.MESSAGE,
                "params": params,
            },
        )
        self._log_created("evaluator", evaluator.name, created)
        return evaluator

    def _seed_evaluation_dataset(self, team) -> EvaluationDataset:
        dataset, created = EvaluationDataset.objects.get_or_create(
            team=team,
            name="Sample Customer Messages",
            defaults={"evaluation_mode": EvaluationMode.MESSAGE},
        )
        self._log_created("dataset", dataset.name, created)
        if created:
            for input_text, output_text in _EVALUATION_DATASET_MESSAGES:
                msg = EvaluationMessage.objects.create(
                    input={"content": input_text, "role": "human"},
                    output={"content": output_text, "role": "ai"},
                )
                dataset.messages.add(msg)
            self.stdout.write(f"    Added {len(_EVALUATION_DATASET_MESSAGES)} sample messages to dataset")
        return dataset

    def _log_created(self, entity_type: str, name: str, created: bool):
        """Helper to log entity creation status."""
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created {entity_type}: {name}"))
        else:
            self.stdout.write(self.style.WARNING(f"  {entity_type.capitalize()} already exists: {name}"))
