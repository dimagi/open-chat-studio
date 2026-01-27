"""
Script to seed sample data for Cypress E2E tests.

This creates sample chatbots, participants, assistants, files, and other test data
to enable the detailed Cypress tests to run successfully.

Run this from the Django project root:
    python cypress/seed_test_data.py
"""

import os
import sys

import django
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")
django.setup()

# NOTE: needs to be below this code but does throw lint error
# Import the create_test_user function from create_test_user.py
# ruff: disable[E402]
import importlib.util  # noqa: E402
import pathlib  # noqa: E402

from apps.assistants.models import OpenAiAssistant  # noqa: E402
from apps.chat.models import ChatMessage, ChatMessageType  # noqa: E402
from apps.documents.models import Collection  # noqa: E402
from apps.experiments.models import Experiment, ExperimentSession, Participant  # noqa: E402
from apps.files.models import File, FilePurpose  # noqa: E402
from apps.pipelines.models import Pipeline  # noqa: E402
from apps.service_providers.models import LlmProvider, LlmProviderModel  # noqa: E402
from apps.teams.models import Team  # noqa: E402

# ruff: enable[E402]

create_test_user_path = pathlib.Path(__file__).parent / "create_test_user.py"
spec = importlib.util.spec_from_file_location("create_test_user", create_test_user_path)
create_test_user_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(create_test_user_module)
create_test_user_func = create_test_user_module.create_test_user


def seed_test_data():
    User = get_user_model()

    # Configuration - must match cypress.env.json
    email = "test@example.com"
    team_slug = "test-team"
    user_exists = User.objects.filter(email=email).exists()
    team_exists = Team.objects.filter(slug=team_slug).exists()

    if not user_exists or not team_exists:
        print("⚠ Test user or team not found. Creating them now...")
        print()
        create_test_user_func()
        print()

    user = User.objects.get(email=email)
    team = Team.objects.get(slug=team_slug)
    print(f"✓ Using user: {email}")
    print(f"✓ Using team: {team_slug}")

    # Get or create default LLM provider
    llm_provider, created = LlmProvider.objects.get_or_create(
        name="OpenAI", team=team, defaults={"type": "openai", "config": {"openai_api_key": "test-key"}}
    )
    if created:
        print(f"✓ Created LLM provider: {llm_provider.name}")
    else:
        print(f"⚠ LLM provider already exists: {llm_provider.name}")

    # Get or create LLM model
    llm_model, created = LlmProviderModel.objects.get_or_create(
        team=team, name="gpt-4", type="openai", defaults={"max_token_limit": 8192}
    )
    if created:
        print(f"✓ Created LLM model: {llm_model.name}")
    else:
        print(f"⚠ LLM model already exists: {llm_model.name}")

    # Create sample pipelines
    print("\n--- Creating Pipelines ---")
    pipeline_names = [
        "Customer Support Pipeline",
        "Language Learning Pipeline",
        "Health Advisory Pipeline",
        "Programming Help Pipeline",
    ]

    pipelines = []
    for name in pipeline_names:
        pipeline, created = Pipeline.objects.get_or_create(
            team=team,
            name=name,
            defaults={
                "data": {"nodes": [], "edges": []},
            },
        )
        if created:
            print(f"  ✓ Created pipeline: {name}")
        else:
            print(f"  ⚠ Pipeline already exists: {name}")
        pipelines.append(pipeline)

    # Create sample experiments (chatbots) - PIPELINE TYPE ONLY
    print("\n--- Creating Pipeline-Based Chatbots (Experiments) ---")
    chatbot_names = [
        "Customer Support Bot",
        "Language Tutor",
        "Health Assistant",
        "Programming Helper",
    ]

    for i, name in enumerate(chatbot_names, 1):
        experiment, created = Experiment.objects.get_or_create(
            team=team,
            name=name,
            defaults={
                "owner": user,
                "description": f"Test pipeline chatbot #{i} for Cypress E2E tests",
                "pipeline": pipelines[i - 1] if i <= len(pipelines) else None,
                "llm_provider": None,  # Pipeline experiments don't use these fields
                "llm_provider_model": None,
            },
        )
        if created:
            print(f"  ✓ Created pipeline experiment: {name}")
        else:
            print(f"  ⚠ Pipeline experiment already exists: {name}")

    # Create sample assistants
    print("\n--- Creating Assistants ---")
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
        if created:
            print(f"  ✓ Created assistant: {name}")
        else:
            print(f"  ⚠ Assistant already exists: {name}")

    # Create sample participants with sessions and chat messages
    print("\n--- Creating Participants with Sessions ---")
    participant_data = [
        {"name": "Alice Johnson", "identifier": "alice.johnson@example.com", "platform": "web"},
        {"name": "Bob Smith", "identifier": "bob.smith@example.com", "platform": "web"},
        {"name": "Carol Williams", "identifier": "carol.williams@example.com", "platform": "api"},
        {"name": "David Brown", "identifier": "david.brown@example.com", "platform": "web"},
        {"name": "Eve Davis", "identifier": "eve.davis@example.com", "platform": "api"},
    ]

    # Get the first experiment for creating sessions
    first_experiment = Experiment.objects.filter(team=team).first()

    for data in participant_data:
        participant, created = Participant.objects.get_or_create(
            team=team,
            platform=data["platform"],
            identifier=data["identifier"],
            defaults={"name": data["name"]},
        )

        if created:
            print(f"  ✓ Created participant: {data['name']} ({data['identifier']})")

            # Create an experiment session with chat for this participant
            if first_experiment:
                session = ExperimentSession.objects.create(
                    team=team, experiment=first_experiment, participant=participant
                )

                # Add some sample messages to the chat
                ChatMessage.objects.create(
                    chat=session.chat,
                    message_type=ChatMessageType.HUMAN,
                    content=f"Hello, I'm {data['name']}. Can you help me?",
                )
                ChatMessage.objects.create(
                    chat=session.chat,
                    message_type=ChatMessageType.AI,
                    content=f"Hello {data['name']}! Of course, I'd be happy to help. What do you need assistance with?",
                )
                ChatMessage.objects.create(
                    chat=session.chat,
                    message_type=ChatMessageType.HUMAN,
                    content="I have a question about your service.",
                )
                print(f"    ✓ Created session and messages for {data['name']}")
        else:
            print(f"  ⚠ Participant already exists: {data['name']}")

    # Create sample files
    print("\n--- Creating Files ---")
    file_names = [
        ("documentation.pdf", "application/pdf", b"Sample PDF content for testing"),
        ("data.csv", "text/csv", b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"),
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"Sample DOCX"),
        ("image.png", "image/png", b"\x89PNG\r\n\x1a\n..."),
    ]

    created_files = []
    for filename, content_type, content in file_names:
        # Create a simple uploaded file
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

        if created:
            print(f"  ✓ Created file: {filename}")
            created_files.append(file_obj)
        else:
            print(f"  ⚠ File already exists: {filename}")
            created_files.append(file_obj)

    # Create sample collection and add files to it
    print("\n--- Creating Collection ---")
    collection_name = "Sample Documents Collection"
    collection, created = Collection.objects.get_or_create(
        team=team,
        name=collection_name,
        defaults={
            "is_index": False,
        },
    )

    if created:
        print(f"  ✓ Created collection: {collection_name}")
        if created_files:
            collection.files.set(created_files)
            print(f"    ✓ Added {len(created_files)} file(s) to collection")
    else:
        print(f"  ⚠ Collection already exists: {collection_name}")
        if created_files:
            collection.files.add(*created_files)
            print(f"    ✓ Ensured {len(created_files)} file(s) in collection")

    print("✅ Test data seeding complete!")
    print("\n" + "=" * 50)
    print("\nYou can now run the Cypress tests:")
    print("  npx cypress open")
    print("  npx cypress run")
    print("\nTo clean up test data later, run:")
    print("  python cypress/cleanup_test_data.py")


if __name__ == "__main__":
    try:
        seed_test_data()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
