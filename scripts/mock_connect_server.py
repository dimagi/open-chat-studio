#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "flask>=3.1.3",
#     "httpx>=0.28.1",
#     "pycryptodome>=3.23.0",
# ]
# ///
"""
Mock CommCare Connect server for local development and testing.

Requires Flask: pip install flask

This script runs a local HTTP server that impersonates the CommCare Connect backend.
It handles all interactions that Open Chat Studio makes with the Connect server, and
can also simulate a user sending encrypted messages to OCS.

Key flow:
  1. OCS calls POST /messaging/create_channel/ when setting up a Connect channel.
  2. This server immediately calls OCS's /api/commcare_connect/generate_key endpoint
     to negotiate the encryption key (OCS calls back to /o/userinfo/ to validate
     the bearer token, then returns the base64-encoded AES-256 key).
  3. OCS sends bot replies via POST /messaging/send_fcm/ — this server decrypts and
     prints them.
  4. Use the interactive prompt to send an encrypted user message to OCS's
     /channels/commcare_connect/incoming_message endpoint.

Usage:
    python scripts/mock_connect_server.py --secret <secret> --server-id <id>

    # Or via environment variables:
    COMMCARE_CONNECT_SERVER_SECRET=... COMMCARE_CONNECT_SERVER_ID=... python scripts/mock_connect_server.py

Set these in your OCS .env to point at this mock:
    COMMCARE_CONNECT_SERVER_URL=http://localhost:9000
    COMMCARE_CONNECT_SERVER_SECRET=<secret>
    COMMCARE_CONNECT_SERVER_ID=<server-id>

Prerequisites:
  - Enable the CommCare Connect feature flag for your team in the OCS admin
    (Admin > Feature Flags > commcare_connect). Without this the Connect channel
    option won't appear and the API endpoints won't be active.
"""

import argparse
import base64
import hashlib
import hmac as hmac_lib
import json
import os
import sys
import threading
import time
import uuid

import httpx
from Crypto.Cipher import AES
from flask import Flask, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global state (in-memory; fine for a dev mock)
# ---------------------------------------------------------------------------

# channel_id -> {"connect_id": str, "channel_source": str, "encryption_key": bytes|None, "consent": bool}
_channels: dict[str, dict] = {}

# Ephemeral bearer token -> connect_id, used during key negotiation callbacks
_tokens: dict[str, str] = {}

_config: dict = {}


# ---------------------------------------------------------------------------
# Crypto helpers (mirror of apps/channels/clients/connect_client.py)
# ---------------------------------------------------------------------------


def _encrypt(key: bytes, message: str) -> tuple[str, str, str]:
    cipher = AES.new(key, AES.MODE_GCM)
    ct, tag = cipher.encrypt_and_digest(message.encode())
    return base64.b64encode(ct).decode(), base64.b64encode(tag).decode(), base64.b64encode(cipher.nonce).decode()


def _decrypt(key: bytes, ciphertext: str, tag: str, nonce: str) -> str:
    cipher = AES.new(key, AES.MODE_GCM, nonce=base64.b64decode(nonce))
    return cipher.decrypt_and_verify(base64.b64decode(ciphertext), base64.b64decode(tag)).decode()


def _hmac_digest(secret: str, body: bytes) -> bytes:
    digest = hmac_lib.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest)


# ---------------------------------------------------------------------------
# Connect server endpoints (called by OCS)
# ---------------------------------------------------------------------------


@app.get("/o/userinfo/")
def userinfo():
    """
    OCS calls this to validate a bearer token and get the connect_id during key negotiation.
    """
    auth = request.headers.get("Authorization") or request.headers.get("AUTHORIZATION")
    connect_id = _tokens.get(auth)
    if not connect_id:
        print(f"  [userinfo] Rejected unknown token: {auth!r}")
        return jsonify({"detail": "Unknown token"}), 401
    print(f"  [userinfo] Validated token for connect_id={connect_id}")
    return jsonify({"sub": connect_id})


@app.post("/messaging/create_channel/")
def create_channel():
    """
    OCS calls this to create a Connect messaging channel for a participant.
    After responding, we call OCS's generate_key endpoint in a background thread
    to negotiate the AES encryption key.
    """
    _verify_basic_auth()
    body = request.get_json()

    connect_id = body.get("connectid", "")
    channel_source = body.get("channel_source", "")
    channel_id = str(uuid.uuid4())

    _channels[channel_id] = {
        "connect_id": connect_id,
        "channel_source": channel_source,
        "encryption_key": None,
        "consent": True,
    }

    print("\n[create_channel] New channel created")
    print(f"  connect_id     = {connect_id}")
    print(f"  channel_source = {channel_source}")
    print(f"  channel_id     = {channel_id}")

    threading.Thread(target=_negotiate_key, args=(channel_id, connect_id), daemon=True).start()

    return jsonify({"channel_id": channel_id, "consent": True})


@app.post("/messaging/send_fcm/")
def send_fcm():
    """
    OCS calls this to deliver an encrypted bot reply to the user via FCM.
    We decrypt and print it.
    """
    _verify_basic_auth()
    body = request.get_json()

    channel_id = body.get("channel", "")
    content = body.get("content", {})
    message_id = body.get("message_id", "")

    channel = _channels.get(channel_id)
    if not channel:
        print(f"\n[send_fcm] Unknown channel_id: {channel_id}")
        return jsonify({"detail": "Channel not found"}), 404

    key = channel.get("encryption_key")
    if not key:
        print(f"\n[send_fcm] No encryption key yet for channel {channel_id}")
        return jsonify({"detail": "Key not yet negotiated"}), 503

    try:
        plaintext = _decrypt(key, content["ciphertext"], content["tag"], content["nonce"])
        print(f"\n[BOT -> USER] {plaintext}")
    except Exception as exc:
        print(f"\n[send_fcm] Decryption error: {exc}")

    return jsonify({"message_id": message_id})


@app.post("/messaging/callback/")
def callback():
    """Delivery callback — OCS registers this but we don't need to act on it."""
    print("[callback] Delivery callback received")
    return "", 200


# ---------------------------------------------------------------------------
# Key negotiation
# ---------------------------------------------------------------------------


def _negotiate_key(channel_id: str, connect_id: str) -> None:
    """
    Call OCS's generate_key endpoint to obtain the AES encryption key for this channel.

    OCS will call back to our /o/userinfo/ endpoint to validate the bearer token
    and retrieve the connect_id before returning the key.
    """
    token = f"mock-bearer-{uuid.uuid4()}"
    _tokens[token] = connect_id

    url = f"{_config['ocs_url']}/api/commcare_connect/generate_key"
    try:
        print(f"  [key-negotiate] POST {url} (channel_id={channel_id})")
        resp = httpx.post(
            url,
            data={"channel_id": channel_id},
            headers={"Authorization": token},
            timeout=15,
        )
        resp.raise_for_status()
        key_b64 = resp.json()["key"]
        _channels[channel_id]["encryption_key"] = base64.b64decode(key_b64)
        print(f"  [key-negotiate] Key received for channel {channel_id}")
        print(f"  [key-negotiate] Key (base64): {key_b64}")
    except Exception as exc:
        print(f"  [key-negotiate] ERROR — could not retrieve key: {exc}")
    finally:
        _tokens.pop(token, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_basic_auth() -> None:
    """Log a warning if Basic Auth credentials don't match expected values."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        print(f"  [auth] WARNING — no Basic Auth header on {request.path}")
        return
    try:
        decoded = base64.b64decode(auth[6:]).decode()
        server_id, secret = decoded.split(":", 1)
        if server_id != _config["server_id"] or secret != _config["secret"]:
            print(f"  [auth] WARNING — Basic Auth credentials mismatch on {request.path}")
    except Exception:
        print("  [auth] WARNING — could not decode Basic Auth header")


# ---------------------------------------------------------------------------
# Sending user messages to OCS
# ---------------------------------------------------------------------------


def send_user_message(channel_id: str, message: str) -> None:
    """Encrypt a message and POST it to OCS as if the user sent it via Connect."""
    channel = _channels.get(channel_id)
    if not channel:
        print(f"[ERROR] Unknown channel_id: {channel_id}")
        print(f"  Known channels: {list(_channels.keys()) or '(none yet)'}")
        return

    key = channel.get("encryption_key")
    if not key:
        print("[ERROR] Encryption key not yet negotiated — wait a moment and try again")
        return

    ciphertext, tag, nonce = _encrypt(key, message)
    payload = {
        "channel_id": channel_id,
        "messages": [
            {
                "timestamp": str(int(time.time())),
                "message_id": str(uuid.uuid4()),
                "ciphertext": ciphertext,
                "tag": tag,
                "nonce": nonce,
            }
        ],
    }

    body = json.dumps(payload).encode()
    digest = _hmac_digest(_config["secret"], body)
    url = f"{_config['ocs_url']}/channels/commcare_connect/incoming_message"

    try:
        print(f"[USER -> OCS] Sending to {url}")
        resp = httpx.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-MAC-DIGEST": digest,
            },
            timeout=15,
        )
        print(f"[USER -> OCS] Response: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"[USER -> OCS] ERROR: {exc}")


# ---------------------------------------------------------------------------
# Interactive CLI loop
# ---------------------------------------------------------------------------


_HELP_LINES = (
    "  send <channel_id> <message>  — encrypt and send a user message",
    "  send <message>               — shorthand when only one channel exists",
    "  list                         — show known channels and key status",
    "  quit                         — stop the server",
)


def _handle_list_command() -> None:
    if not _channels:
        print("  (no channels yet)")
        return
    for cid, ch in _channels.items():
        key_status = "ready" if ch["encryption_key"] else "pending"
        print(f"  {cid}  connect_id={ch['connect_id']}  key={key_status}  consent={ch['consent']}")


def _handle_send_command(parts: list[str]) -> None:
    if len(_channels) == 1 and len(parts) >= 2:
        channel_id = next(iter(_channels))
        message = " ".join(parts[1:])
    elif len(parts) >= 3:
        channel_id = parts[1]
        message = parts[2]
    else:
        print("  Usage: send <channel_id> <message>")
        return
    send_user_message(channel_id, message)


def _handle_help_command() -> None:
    for line in _HELP_LINES:
        print(line)


def _interactive_loop() -> None:
    print("\nServer is running. Use the prompt below to send user messages to OCS.")
    print("Commands:")
    _handle_help_command()
    print()

    handlers = {
        "list": lambda parts: _handle_list_command(),
        "send": _handle_send_command,
        "help": lambda parts: _handle_help_command(),
    }

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down...")
            return

        if not line:
            continue

        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            print("Shutting down...")
            return

        handler = handlers.get(cmd)
        if handler is None:
            print(f"  Unknown command: {cmd!r}. Type 'help' for usage.")
        else:
            handler(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock CommCare Connect server for OCS local development")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_CONNECT_PORT", 9000)))
    parser.add_argument("--ocs-url", default=os.environ.get("OCS_URL", "http://localhost:8000"))
    parser.add_argument("--secret", default=os.environ.get("COMMCARE_CONNECT_SERVER_SECRET", ""))
    parser.add_argument("--server-id", default=os.environ.get("COMMCARE_CONNECT_SERVER_ID", ""))
    args = parser.parse_args()

    if not args.secret:
        print("ERROR: --secret / COMMCARE_CONNECT_SERVER_SECRET is required")
        sys.exit(1)
    if not args.server_id:
        print("ERROR: --server-id / COMMCARE_CONNECT_SERVER_ID is required")
        sys.exit(1)

    _config["ocs_url"] = args.ocs_url.rstrip("/")
    _config["secret"] = args.secret
    _config["server_id"] = args.server_id

    print(f"Mock CommCare Connect server on http://localhost:{args.port}")
    print(f"  OCS URL   : {_config['ocs_url']}")
    print(f"  Server ID : {_config['server_id']}")
    print()
    print("OCS .env settings:")
    print(f"  COMMCARE_CONNECT_SERVER_URL=http://localhost:{args.port}")
    print("  COMMCARE_CONNECT_SERVER_SECRET=***REDACTED***")
    print(f"  COMMCARE_CONNECT_SERVER_ID={_config['server_id']}")
    print(f"  COMMCARE_CONNECT_GET_CONNECT_ID_URL=http://localhost:{args.port}/o/userinfo/")

    threading.Thread(
        target=lambda: app.run(port=args.port, use_reloader=False),
        daemon=True,
    ).start()

    _interactive_loop()


if __name__ == "__main__":
    main()
