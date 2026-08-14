# Locust Scripts

This folder contains scripts for load testing Open Chat Studio.

## Chat API Load Test (`chat_api_load_test.py`)

Simulates multiple users having conversations with chatbots via the public chat API.

### Quick Start

```bash
# Install locust
uv pip install -r requirements.txt

# Run test with chatbot ID(s)
CHATBOT_IDS="your-chatbot-uuid-here" locust -f locust/chat_api_load_test.py
```

Open http://localhost:8089 to configure users and start the test.

### Getting Chatbot IDs

Use the helper script to find available chatbot UUIDs via the API:

```bash
# List all chatbots (will prompt for API key)
python locust/get_chatbot_ids.py

# Use API key from environment variable
OCS_API_KEY=your-key python locust/get_chatbot_ids.py

# Connect to a different instance
OCS_API_KEY=your-key python locust/get_chatbot_ids.py --base-url https://www.openchatstudio.com

# Export as environment variable
eval $(OCS_API_KEY=your-key python locust/get_chatbot_ids.py --format env)
```

### Configuration

Environment variables:
- `CHATBOT_IDS` (required): Comma-separated chatbot UUIDs
- `MESSAGES_PER_SESSION`: Messages per conversation (default: 10)
- `MIN_WAIT` / `MAX_WAIT`: Wait time between messages in seconds (default: 2-5)
- `EMBED_KEY`: Optional authentication key

### Examples

```bash
# Multiple chatbots, 10 users, 5 minute run
CHATBOT_IDS="uuid1,uuid2" locust -f locust/chat_api_load_test.py \
  --host http://localhost:8000 --users 10 --spawn-rate 2 --run-time 5m

# Headless mode with HTML report
CHATBOT_IDS="uuid1" locust -f locust/chat_api_load_test.py \
  --host http://localhost:8000 --users 50 --spawn-rate 5 \
  --run-time 10m --headless --html report.html

# Quick stress test
CHATBOT_IDS="uuid1" MESSAGES_PER_SESSION=5 locust -f locust/chat_api_load_test.py \
  --users 100 --spawn-rate 10 --user-classes QuickChatUser
```
