#!/usr/bin/env python
"""
Helper script to get chatbot IDs for load testing via the REST API.

This script lists available chatbots (experiments) with their IDs.

Usage:
    python locust/get_chatbot_ids.py
    python locust/get_chatbot_ids.py --base-url https://chatbots.dimagi.com
    python locust/get_chatbot_ids.py --format env
    OCS_API_KEY=your-key python locust/get_chatbot_ids.py --format list
"""

import argparse
import getpass
import os
import sys

import httpx


def get_api_key():
    """Get API key from environment or prompt user."""
    api_key = os.getenv("OCS_API_KEY")
    if not api_key:
        api_key = getpass.getpass("Enter your API key: ")
    return api_key


def get_chatbots(base_url, api_key):
    """
    Get all chatbots from the API.

    Args:
        base_url: Base URL for the API (e.g., https://www.openchatstudio.com)
        api_key: API key for authentication

    Returns:
        List of chatbot dictionaries with 'id', 'name', and 'url' keys
    """
    chatbots = []
    url = f"{base_url}/api/experiments/"

    headers = {
        "X-api-key": api_key,
        "Accept": "application/json",
    }

    while url:
        try:
            response = httpx.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except httpx.RequestError as e:
            print(f"Error fetching chatbots: {e}", file=sys.stderr)
            sys.exit(1)

        data = response.json()
        chatbots.extend(data.get("results", []))

        # Handle pagination
        url = data.get("next")

    return chatbots


def format_table(chatbots):
    """Format chatbots as a table."""
    if not chatbots:
        print("No chatbots found.")
        return

    # Calculate column widths
    max_name = max(len(cb["name"]) for cb in chatbots)
    max_name = max(max_name, len("Chatbot Name"))

    # Print header
    print(f"{'Chatbot Name':<{max_name}} | Version | UUID")
    print(f"{'-' * max_name}-+-{'-' * 7}-+{'-' * 36}")

    # Print rows
    for cb in chatbots:
        version = cb.get("version_number", "N/A")
        print(f"{cb['name']:<{max_name}} | {version:>7} | {cb['id']}")

    print()
    print(f"Total: {len(chatbots)} chatbot(s)")


def format_env(chatbots):
    """Format chatbots as environment variable export."""
    if not chatbots:
        print("# No chatbots found.")
        return

    ids = ",".join(str(cb["id"]) for cb in chatbots)
    print(f'export CHATBOT_IDS="{ids}"')
    print()
    print("# Individual chatbot IDs:")
    for cb in chatbots:
        safe_name = cb["name"].replace(" ", "_").replace("-", "_").upper()
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        version = cb.get("version_number", "")
        print(f"# {cb['name']} (v{version})")
        print(f'export CHATBOT_ID_{safe_name}="{cb["id"]}"')


def format_list(chatbots):
    """Format chatbots as a simple list of IDs."""
    if not chatbots:
        print("No chatbots found.")
        return

    for cb in chatbots:
        print(cb["id"])


def main():
    parser = argparse.ArgumentParser(
        description="Get chatbot IDs for load testing via the REST API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # List all chatbots (will prompt for API key)
    python locust/get_chatbot_ids.py

    # Use API key from environment
    OCS_API_KEY=your-key python locust/get_chatbot_ids.py

    # Specify a different base URL
    python locust/get_chatbot_ids.py --base-url https://chatbots.dimagi.com

    # Export as environment variable
    OCS_API_KEY=your-key python locust/get_chatbot_ids.py --format env

    # Use in locust command
    eval $(OCS_API_KEY=your-key python locust/get_chatbot_ids.py --format env)
    locust -f locust/chat_api_load_test.py

    # Get just the IDs
    python locust/get_chatbot_ids.py --format list

Note:
    Set OCS_API_KEY environment variable to avoid password prompt.
    Get your API key from: {base_url}/a/{team}/settings/api_keys/
        """,
    )

    parser.add_argument(
        "--base-url",
        default="https://www.openchatstudio.com",
        help="Base URL for the API (default: https://www.openchatstudio.com)",
    )

    parser.add_argument(
        "--format",
        choices=["table", "env", "list"],
        default="table",
        help="Output format (default: table)",
    )

    parser.add_argument(
        "--api-key",
        help="API key for authentication (can also use OCS_API_KEY env var)",
    )

    args = parser.parse_args()

    try:
        # Get API key
        api_key = args.api_key or get_api_key()

        if not api_key:
            print("Error: API key is required", file=sys.stderr)
            sys.exit(1)

        # Strip trailing slash from base URL
        base_url = args.base_url.rstrip("/")

        # Fetch chatbots
        chatbots = get_chatbots(base_url, api_key)

        # Format output
        if args.format == "table":
            format_table(chatbots)
        elif args.format == "env":
            format_env(chatbots)
        elif args.format == "list":
            format_list(chatbots)

    except KeyboardInterrupt:
        print("\nCancelled", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
