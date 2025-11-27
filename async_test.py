import argparse
import asyncio
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")
django.setup()


async def main(args):
    """Main asynchronous function for the CLI."""
    print("Starting async test...")
    from apps.channels.models import ChannelPlatform, ExperimentChannel
    from apps.channels.tasks import ahandle_api_message
    from apps.experiments.models import Experiment
    from apps.users.models import CustomUser

    user = await CustomUser.objects.aget(email=args.username)
    chatbot = await Experiment.objects.select_related("team", "pipeline").aget(public_id=args.chatbot_id)
    team = chatbot.team

    # Verify it has a pipeline
    if not chatbot.pipeline:
        print("ERROR: This experiment does not have a pipeline. Async POC only supports PipelineBot.")
        return

    channel, _ = await ExperimentChannel.objects.aget_or_create(
        team=team, platform=ChannelPlatform.API, name=f"{team.slug}-api-channel"
    )
    message = args.message or "Hello, how are you?"
    participant_id = args.participant_id or user.email

    print(f"Sending message: {message}")
    print(f"Pipeline: {chatbot.pipeline.name}")

    import time

    start = time.time()
    response = await ahandle_api_message(user, chatbot, channel, message, participant_id)
    end = time.time()

    print(f"\nResponse: {response.content}")
    print(f"Message ID: {response.id if response.id else 'Not saved'}")
    print(f"Time taken: {end - start:.2f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="An async CLI test script.")
    parser.add_argument("--username", type=str, required=True, help="Email of the user")
    parser.add_argument("--chatbot-id", type=str, required=True, help="Public ID of the chatbot to test")
    parser.add_argument("--message", type=str, help="Message to send (default: 'Hello, how are you?')")
    parser.add_argument("--participant-id", type=str, help="Participant ID (default: user email)")

    args = parser.parse_args()
    asyncio.run(main(args))
