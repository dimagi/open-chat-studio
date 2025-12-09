"""
Locust load test for Open Chat Studio chat API.

This script simulates multiple users having conversations with chatbots.

Configuration via environment variables:
    CHATBOT_IDS: Comma-separated list of chatbot UUIDs (required)
    API_BASE_URL: Base URL for the API (default: http://localhost:8000)
    MIN_WAIT: Minimum wait time between messages in seconds (default: 2)
    MAX_WAIT: Maximum wait time between messages in seconds (default: 5)
    MESSAGES_PER_SESSION: Number of messages per conversation (default: 10)
    EMBED_KEY: Optional embed key for authentication

Example usage:
    CHATBOT_IDS="uuid1,uuid2" locust -f locust/chat_api_load_test.py
    CHATBOT_IDS="uuid1" MESSAGES_PER_SESSION=15 locust -f locust/chat_api_load_test.py --users 10 --spawn-rate 2
"""

import logging
import os
import random
import time
import uuid

from locust import HttpUser, between, task

logger = logging.getLogger(__name__)


class ChatUser(HttpUser):
    """
    Simulates a user chatting with a chatbot through the API.

    Each user will:
    1. Start a new session with a random chatbot
    2. Send multiple messages and wait for responses
    3. Complete a full conversation before ending
    """

    # Wait time between messages (in seconds)
    wait_time = between(int(os.getenv("MIN_WAIT", "2")), int(os.getenv("MAX_WAIT", "5")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id: str | None = None
        self.participant_remote_id: str = str(uuid.uuid4())
        self.participant_name: str = f"TestUser_{uuid.uuid4().hex[:8]}"
        self.messages_sent: int = 0
        self.max_messages: int = int(os.getenv("MESSAGES_PER_SESSION", "10"))
        self.chatbot_ids: list[str] = []
        self.embed_key: str | None = os.getenv("EMBED_KEY")

        # Predefined conversation messages
        self.conversation_messages = [
            "Hello, can you help me?",
            "What services do you offer?",
            "Tell me more about that.",
            "How much does it cost?",
            "What are the next steps?",
            "Can you explain in more detail?",
            "That sounds interesting.",
            "Do you have any examples?",
            "What are the benefits?",
            "How long does it take?",
            "Are there any requirements?",
            "Can you help me get started?",
            "What if I have questions later?",
            "Is there a trial period?",
            "Thank you for the information.",
        ]

    def on_start(self):
        """Called when a simulated user starts."""
        # Load chatbot IDs from environment
        chatbot_ids_str = os.getenv("CHATBOT_IDS", "")
        if not chatbot_ids_str:
            raise ValueError("CHATBOT_IDS environment variable must be set")

        self.chatbot_ids = [cid.strip() for cid in chatbot_ids_str.split(",")]
        logger.info(f"User {self.participant_name} starting with {len(self.chatbot_ids)} chatbot(s)")

        # Start a new chat session
        self.start_session()

    def get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        headers = {
            "Content-Type": "application/json",
            "x-ocs-widget-version": "locust-test-1.0",
        }

        if self.embed_key:
            headers["X-Embed-Key"] = self.embed_key

        return headers

    def start_session(self) -> bool:
        """
        Start a new chat session with a random chatbot.

        Returns:
            True if session started successfully, False otherwise
        """
        chatbot_id = random.choice(self.chatbot_ids)

        payload = {
            "chatbot_id": chatbot_id,
            "participant_remote_id": self.participant_remote_id,
            "participant_name": self.participant_name,
            "session_data": {
                "source": "locust_test",
                "test_run": str(uuid.uuid4()),
            },
        }

        with self.client.post(
            "/api/chat/start/", json=payload, headers=self.get_headers(), catch_response=True, name="Start Session"
        ) as response:
            if response.status_code == 201:
                data = response.json()
                self.session_id = data.get("session_id")
                logger.info(f"Session started: {self.session_id} for chatbot {chatbot_id}")
                response.success()
                return True
            else:
                logger.error(f"Failed to start session: {response.status_code} - {response.text}")
                response.failure(f"Failed to start session: {response.status_code}")
                return False

    def send_message(self, message_text: str) -> str | None:
        """
        Send a message to the chatbot.

        Args:
            message_text: The message to send

        Returns:
            Task ID if successful, None otherwise
        """
        if not self.session_id:
            logger.error("Cannot send message: No active session")
            return None

        payload = {"message": message_text}

        with self.client.post(
            f"/api/chat/{self.session_id}/message/",
            json=payload,
            headers=self.get_headers(),
            catch_response=True,
            name="Send Message",
        ) as response:
            if response.status_code == 202:
                data = response.json()
                task_id = data.get("task_id")
                logger.debug(f"Message sent, task_id: {task_id}")
                response.success()
                return task_id
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                response.failure(f"Failed to send message: {response.status_code}")
                return None

    def poll_task_response(self, task_id: str, max_attempts: int = 120, poll_interval: float = 1.0) -> bool:
        """
        Poll for task completion and get the bot's response.

        Args:
            task_id: The task ID to poll
            max_attempts: Maximum number of polling attempts
            poll_interval: Time to wait between polls in seconds

        Returns:
            True if response received successfully, False otherwise
        """
        if not self.session_id:
            logger.error("Cannot poll: No active session")
            return False

        for attempt in range(max_attempts):
            with self.client.get(
                f"/api/chat/{self.session_id}/{task_id}/poll/",
                headers=self.get_headers(),
                catch_response=True,
                name="Poll Task Response",
            ) as response:
                if response.status_code != 200:
                    logger.error(f"Poll failed: {response.status_code} - {response.text}")
                    response.failure(f"Poll failed: {response.status_code}")
                    return False

                data = response.json()
                status = data.get("status")

                if status == "complete":
                    message = data.get("message")
                    if message:
                        logger.debug(f"Received response: {message.get('content', '')[:50]}...")
                        response.success()
                        return True
                    else:
                        logger.error("Response complete but no message received")
                        response.failure("No message in complete response")
                        return False

                elif status == "error":
                    error = data.get("error", "Unknown error")
                    logger.error(f"Task error: {error}")
                    response.failure(f"Task error: {error}")
                    return False

                elif status == "processing":
                    # Still processing, continue polling
                    response.success()
                    if attempt < max_attempts - 1:
                        time.sleep(poll_interval)
                    continue
                else:
                    logger.error(f"Unknown status: {status}")
                    response.failure(f"Unknown status: {status}")
                    return False

        logger.error(f"Task {task_id} timed out after {max_attempts} attempts")
        return False

    @task
    def have_conversation(self):
        """
        Main task: Have a conversation with the chatbot.

        This task sends messages and waits for responses until
        the conversation reaches the configured message count.
        """
        # Ensure we have an active session
        if not self.session_id:
            logger.warning("No active session, starting new one")
            if not self.start_session():
                return

        # Check if conversation is complete
        if self.messages_sent >= self.max_messages:
            logger.info(f"Conversation complete for session {self.session_id} ({self.messages_sent} messages)")
            # Reset for next conversation
            self.messages_sent = 0
            self.session_id = None
            self.participant_remote_id = str(uuid.uuid4())
            self.participant_name = f"TestUser_{uuid.uuid4().hex[:8]}"
            return

        # Get next message
        message_text = random.choice(self.conversation_messages)

        # Send message and wait for response
        logger.info(f"Sending message {self.messages_sent + 1}/{self.max_messages}: {message_text[:50]}...")
        with self.environment.events.request.measure("TASK", "send_message_task"):
            task_id = self.send_message(message_text)

            if task_id:
                # Poll for response
                if self.poll_task_response(task_id):
                    self.messages_sent += 1
                    logger.info(f"Message {self.messages_sent}/{self.max_messages} completed")
                else:
                    logger.error("Failed to get response, ending conversation")
                    self.messages_sent = 0
                    self.session_id = None
            else:
                logger.error("Failed to send message, ending conversation")
                self.messages_sent = 0
                self.session_id = None


class QuickChatUser(ChatUser):
    """
    Faster variant of ChatUser for stress testing.

    Uses shorter wait times and fewer messages per session.
    """

    wait_time = between(0.5, 2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_messages = int(os.getenv("MESSAGES_PER_SESSION", "5"))
