#!/bin/bash
# Example script to run chat API load test
# This script demonstrates a complete workflow for running the load test

set -e

echo "=== Open Chat Studio - Chat API Load Test ==="
echo ""

# Check if locust is installed
if ! command -v locust &> /dev/null; then
    echo "Error: locust is not installed"
    echo "Install with: pip install -r locust/requirements.txt"
    exit 1
fi

# Get API key if not already set
if [ -z "$OCS_API_KEY" ]; then
    echo "API key not found in environment."
    echo "Enter your API key (or set OCS_API_KEY environment variable):"
    read -rs OCS_API_KEY
    export OCS_API_KEY
    echo ""
fi

# Get base URL
echo "API Base URL (press Enter for https://www.openchatstudio.com):"
read -r BASE_URL
BASE_URL=${BASE_URL:-https://www.openchatstudio.com}

# Get chatbot IDs
echo ""
echo "Available chatbots:"
echo "-------------------"
python locust/get_chatbot_ids.py --base-url "$BASE_URL"

echo ""
echo "-------------------"
echo ""
echo "Enter chatbot ID(s) to test (comma-separated for multiple):"
read -r CHATBOT_IDS

if [ -z "$CHATBOT_IDS" ]; then
    echo "Error: No chatbot ID provided"
    exit 1
fi

# Ask for configuration
echo ""
echo "Number of concurrent users (default: 10):"
read -r USERS
USERS=${USERS:-10}

echo "Spawn rate (users per second, default: 2):"
read -r SPAWN_RATE
SPAWN_RATE=${SPAWN_RATE:-2}

echo "Messages per conversation (default: 10):"
read -r MESSAGES
MESSAGES=${MESSAGES:-10}

echo "Run time (e.g., 5m, 1h, 300s, default: 5m):"
read -r RUN_TIME
RUN_TIME=${RUN_TIME:-5m}

echo "Target host (press Enter to use $BASE_URL):"
read -r HOST
HOST=${HOST:-$BASE_URL}

# Optional: Embed key
echo "Embed key (press Enter to skip):"
read -r EMBED_KEY

# Build the command
CMD="CHATBOT_IDS=\"$CHATBOT_IDS\" MESSAGES_PER_SESSION=$MESSAGES"

if [ -n "$EMBED_KEY" ]; then
    CMD="$CMD EMBED_KEY=\"$EMBED_KEY\""
fi

CMD="$CMD locust -f locust/chat_api_load_test.py"
CMD="$CMD --host $HOST"
CMD="$CMD --users $USERS"
CMD="$CMD --spawn-rate $SPAWN_RATE"
CMD="$CMD --run-time $RUN_TIME"

# Ask for mode
echo ""
echo "Run mode:"
echo "  1) Interactive (with web UI at http://localhost:8089)"
echo "  2) Headless (no web UI, auto-start)"
read -r MODE

if [ "$MODE" = "2" ]; then
    echo "HTML report filename (default: report.html):"
    read -r REPORT
    REPORT=${REPORT:-report.html}
    CMD="$CMD --headless --html $REPORT"
fi

# Show the command
echo ""
echo "=== Running Load Test ==="
echo "Command: $CMD"
echo ""
echo "Press Enter to start, or Ctrl+C to cancel..."
read -r

# Run the test
eval "$CMD"

echo ""
echo "=== Load Test Complete ==="
if [ "$MODE" = "2" ]; then
    echo "Report saved to: $REPORT"
fi
