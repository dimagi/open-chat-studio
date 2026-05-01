"""
Open Chat Studio — session cost estimator
=========================================

Walks existing session data for one or more OCS experiments and estimates what
each session *would have cost* on different LLMs by re-tokenising the full
conversation and applying per-model pricing.

Approach
--------
For every session we walk messages in order. Every AI message is a "turn". For
each turn we charge:

    input_tokens  = system_prompt_tokens + sum(tokens of all prior messages)
                    + per-message overhead × (prior_message_count + 1)
    output_tokens = tokens of this AI message

Summed across turns this captures that history grows (quadratically) each turn
— the dominant driver of real chat cost.

Tokeniser strategy
------------------
- OpenAI + Google (fallback): tiktoken, using the model's native encoding where
  known (o200k_base for 4o/5-family, cl100k_base for older).
- Anthropic Claude: Anthropic's `messages.count_tokens` API. Accurate, but
  network-bound — one call per message. Results are cached in-process.

Caveats
-------
- Pricing table below is a snapshot — verify against provider pages before
  trusting. Edit MODELS in one place.
- OCS pipelines can run multiple LLM calls per user turn (routing, summarisation,
  RAG re-ranking, tool calls). This script models a single-LLM-node pipeline.
  If a session is served by a more complex pipeline the real cost is higher
  than the estimate here.
- Token count for Gemini via tiktoken o200k_base is a rough proxy.
- Field names marked `# TODO: verify` are my best guess from the OCS API shape
  — double-check against https://www.openchatstudio.com/api/schema/ and adjust
  the two constants at the top of OCSClient.

Usage
-----
    export OCS_API_KEY=...
    python ocs_cost_estimator.py --experiment <exp_id> [--experiment <exp_id>...]
    python ocs_cost_estimator.py --all-experiments --limit-sessions 100
    python ocs_cost_estimator.py --experiment abc --csv out.csv

Dependencies
------------
    pip install requests tiktoken anthropic
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import requests

try:
    import tiktoken
except ImportError:
    sys.exit("Missing dependency: pip install tiktoken")

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Pricing — USD per 1M tokens. Verify against provider pricing pages.
# ---------------------------------------------------------------------------
# Structure: model_id -> (input_price_per_1M, output_price_per_1M, tokeniser)
# `tokeniser` is one of: "openai", "anthropic", "google"
# The `encoding` field only applies when tokeniser == "openai" (or "google",
# which we approximate with tiktoken).

MODELS: dict[str, dict[str, Any]] = {
    # OpenAI — verified against https://developers.openai.com/api/docs/pricing (Apr 2026)
    # and https://developers.openai.com/api/docs/models
    "gpt-4.1": {
        "in": 2.00,
        "out": 8.00,
        "tokeniser": "openai",
        "encoding": "o200k_base",
    },
    "gpt-5.4": {
        "in": 2.50,
        "out": 15.00,
        "tokeniser": "openai",
        "encoding": "o200k_base",
    },
    # Anthropic — verified against https://platform.claude.com/docs/en/about-claude/pricing
    # Note: Opus 4.5+ dropped from $15/$75 to $5/$25. Token counts come from the
    # count_tokens API for accuracy.
    # Opus 4.7 uses a NEW tokenizer (1.0x–1.35x more tokens than 4.6 for identical
    # text). Same sticker price as 4.6, but real cost on a given session will run
    # higher. count_tokens uses the model-specific tokenizer, so this is reflected
    # automatically when api_name="claude-opus-4-7".
    "claude-sonnet-4.6": {
        "in": 3.00,
        "out": 15.00,
        "tokeniser": "anthropic",
        "api_name": "claude-sonnet-4-6",
    },
    "claude-opus-4.6": {
        "in": 5.00,
        "out": 25.00,
        "tokeniser": "anthropic",
        "api_name": "claude-opus-4-6",
    },
    "claude-opus-4.7": {
        "in": 5.00,
        "out": 25.00,
        "tokeniser": "anthropic",
        "api_name": "claude-opus-4-7",
    },
}

# OpenAI-style chat-format per-message overhead. Approximation; 3-4 tokens for
# the role/content wrapping plus ~3 priming tokens at the end.
PER_MESSAGE_OVERHEAD = 4
PER_CONVERSATION_OVERHEAD = 3


# ---------------------------------------------------------------------------
# OCS API client
# ---------------------------------------------------------------------------


@dataclass
class OCSClient:
    base_url: str
    api_key: str
    timeout: int = 30

    # --- TODO: verify these two against the schema -------------------------
    # OCS exposes paginated list endpoints for experiments and sessions.
    # The session-detail payload is expected to embed (or to allow ?expand=)
    # the list of messages with a role and a content field.
    EXPERIMENTS_PATH = "/api/experiments/"
    SESSIONS_PATH = "/api/sessions/"
    MESSAGES_FIELD = "messages"  # key on the session-detail response
    MESSAGE_ROLE_FIELD = "role"  # "human" / "ai" / "system"
    MESSAGE_CONTENT_FIELD = "content"
    HUMAN_ROLES = {"human", "user"}
    AI_ROLES = {"ai", "assistant", "bot"}
    # ----------------------------------------------------------------------

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            }
        )
        return s

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Walk DRF-style paginated list responses (`next` URL or ?page=N)."""
        sess = self._session()
        url = self.base_url.rstrip("/") + path
        params = dict(params or {})
        while url:
            r = sess.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            # DRF pagination: {"next": url, "results": [...]}
            # Non-paginated fallback: either a list or {"results": [...]}
            if isinstance(payload, list):
                yield from payload
                return
            yield from payload.get("results", [])
            url = payload.get("next")
            params = {}  # next URL already carries query params

    def list_experiments(self) -> Iterator[dict]:
        yield from self._paginate(self.EXPERIMENTS_PATH)

    def list_sessions(self, experiment_id: str, limit: int | None = None) -> Iterator[dict]:
        # OCS likely supports filtering sessions by experiment. Adjust the
        # param name if the schema uses something different.
        params = {"experiment": experiment_id}
        for count, session in enumerate(self._paginate(self.SESSIONS_PATH, params=params), start=1):
            yield session
            if limit and count >= limit:
                return

    def get_session_detail(self, session_id: str) -> dict:
        sess = self._session()
        url = f"{self.base_url.rstrip('/')}{self.SESSIONS_PATH}{session_id}/"
        r = sess.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_messages(self, session: dict) -> list[tuple[str, str]]:
        """Return [(role, content), ...] for a session. Fetches detail if the
        list-response doesn't embed messages."""
        msgs = session.get(self.MESSAGES_FIELD)
        if msgs is None:
            detail = self.get_session_detail(session["id"])
            msgs = detail.get(self.MESSAGES_FIELD, [])
        out = []
        for m in msgs:
            role = (m.get(self.MESSAGE_ROLE_FIELD) or "").lower()
            content = m.get(self.MESSAGE_CONTENT_FIELD) or ""
            if role and content:
                out.append((role, content))
        return out


# ---------------------------------------------------------------------------
# Tokenisers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _tiktoken_encoder(name: str):
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens_tiktoken(text: str, encoding: str = "o200k_base") -> int:
    if not text:
        return 0
    return len(_tiktoken_encoder(encoding).encode(text))


class AnthropicTokenCounter:
    """Wraps the Anthropic messages.count_tokens API with in-process caching.

    count_tokens is billed per call on Anthropic's side but at a very low rate.
    We cache by (model, content-hash) so repeated messages across sessions only
    hit the API once per run.
    """

    def __init__(self, api_key: str | None = None):
        if anthropic is None:
            sys.exit("Missing dependency for Anthropic token counting: pip install anthropic")
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._cache: dict[tuple[str, int], int] = {}

    def count(self, text: str, api_model: str) -> int:
        if not text:
            return 0
        key = (api_model, hash(text))
        if key in self._cache:
            return self._cache[key]
        # count_tokens wants a messages array; role is required but doesn't
        # affect the count materially beyond chat-format overhead.
        resp = self._client.messages.count_tokens(
            model=api_model,
            messages=[{"role": "user", "content": text}],
        )
        n = resp.input_tokens
        # Strip the small fixed overhead the API adds for the envelope so we
        # can apply our own PER_MESSAGE_OVERHEAD consistently across providers.
        n = max(0, n - 3)
        self._cache[key] = n
        return n


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


@dataclass
class TurnCost:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class SessionCost:
    session_id: str
    experiment_id: str
    turn_count: int
    total_input_tokens: int
    total_output_tokens: int
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


@dataclass
class ModelRun:
    model: str
    sessions: list[SessionCost] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(s.cost_usd for s in self.sessions)

    @property
    def total_input(self) -> int:
        return sum(s.total_input_tokens for s in self.sessions)

    @property
    def total_output(self) -> int:
        return sum(s.total_output_tokens for s in self.sessions)


def tokens_for_message(
    text: str,
    model_config: dict,
    anthropic_counter: AnthropicTokenCounter | None,
) -> int:
    t = model_config["tokeniser"]
    if t == "anthropic":
        assert anthropic_counter is not None, "Anthropic counter required for claude models"
        return anthropic_counter.count(text, model_config["api_name"])
    # openai + google (approximated)
    return count_tokens_tiktoken(text, model_config.get("encoding", "o200k_base"))


def estimate_session_cost(
    messages: list[tuple[str, str]],
    model: str,
    system_prompt: str,
    anthropic_counter: AnthropicTokenCounter | None,
) -> SessionCost:
    """Walk messages, charging each AI response for full history-so-far input."""
    cfg = MODELS[model]
    client = OCSClient  # for role constants only

    # Pre-tokenise everything once.
    system_tokens = tokens_for_message(system_prompt, cfg, anthropic_counter) if system_prompt else 0
    msg_tokens: list[int] = [tokens_for_message(c, cfg, anthropic_counter) for _, c in messages]

    total_input = 0
    total_output = 0
    turns = 0

    for idx, (role, _content) in enumerate(messages):
        if role not in client.AI_ROLES:
            continue

        # Input is: system prompt + every message strictly before this AI one.
        history_tokens = sum(msg_tokens[:idx])
        history_messages = idx  # count of preceding messages
        input_tokens = (
            system_tokens
            + history_tokens
            + PER_MESSAGE_OVERHEAD * (history_messages + (1 if system_prompt else 0))
            + PER_CONVERSATION_OVERHEAD
        )
        output_tokens = msg_tokens[idx]

        total_input += input_tokens
        total_output += output_tokens
        turns += 1

    cost = total_input * cfg["in"] / 1_000_000 + total_output * cfg["out"] / 1_000_000

    return SessionCost(
        session_id="",  # filled in by caller
        experiment_id="",  # filled in by caller
        turn_count=turns,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--base-url",
        default=os.environ.get("OCS_BASE_URL", "https://www.openchatstudio.com"),
        help="OCS base URL (env OCS_BASE_URL)",
    )
    p.add_argument("--api-key", default=os.environ.get("OCS_API_KEY"), help="OCS API key (env OCS_API_KEY)")
    p.add_argument(
        "--experiment", action="append", default=[], help="Experiment ID to analyse. Can be passed multiple times."
    )
    p.add_argument("--all-experiments", action="store_true", help="Analyse every experiment returned by the API.")
    p.add_argument(
        "--limit-sessions", type=int, default=None, help="Cap sessions per experiment (useful for smoke tests)."
    )
    p.add_argument(
        "--min-messages", type=int, default=10, help="Skip sessions with fewer than this many messages. Default: 10."
    )
    p.add_argument(
        "--models", default=",".join(MODELS.keys()), help="Comma-separated model IDs to compare. Default: all."
    )
    p.add_argument("--system-prompt", default="", help="System prompt to add to input-token accounting for every turn.")
    p.add_argument("--csv", default=None, help="Write per-session rows to this CSV path.")
    return p


def resolve_experiment_ids(client: OCSClient, args) -> list[str]:
    if args.all_experiments:
        return [e["id"] for e in client.list_experiments()]
    if args.experiment:
        return args.experiment
    sys.exit("Pass --experiment <id> (repeatable) or --all-experiments")


def run(args) -> int:
    if not args.api_key:
        sys.exit("Missing OCS API key. Set OCS_API_KEY or pass --api-key.")

    requested_models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in requested_models if m not in MODELS]
    if unknown:
        sys.exit(f"Unknown model(s): {unknown}. Known: {list(MODELS)}")

    anthropic_counter: AnthropicTokenCounter | None = None
    if any(MODELS[m]["tokeniser"] == "anthropic" for m in requested_models):
        anthropic_counter = AnthropicTokenCounter()

    client = OCSClient(base_url=args.base_url, api_key=args.api_key)
    experiment_ids = resolve_experiment_ids(client, args)

    runs = {m: ModelRun(model=m) for m in requested_models}
    all_rows: list[tuple[str, str, int, dict[str, SessionCost]]] = []
    message_counts: list[int] = []
    skipped_short = 0

    for exp_id in experiment_ids:
        print(f"Experiment {exp_id}: fetching sessions...", file=sys.stderr)
        sessions = list(client.list_sessions(exp_id, limit=args.limit_sessions))
        print(f"  {len(sessions)} sessions", file=sys.stderr)

        for i, session in enumerate(sessions, 1):
            session_id = session.get("id") or session.get("external_id") or f"<s{i}>"
            try:
                messages = client.get_messages(session)
            except requests.HTTPError as e:
                print(f"  skip {session_id}: {e}", file=sys.stderr)
                continue
            if not messages:
                continue
            if len(messages) < args.min_messages:
                skipped_short += 1
                continue

            message_counts.append(len(messages))

            per_model: dict[str, SessionCost] = {}
            for m in requested_models:
                cost = estimate_session_cost(messages, m, args.system_prompt, anthropic_counter)
                cost.session_id = session_id
                cost.experiment_id = exp_id
                runs[m].sessions.append(cost)
                per_model[m] = cost

            all_rows.append((exp_id, session_id, len(messages), per_model))

            if i % 10 == 0:
                print(f"  ...{i}/{len(sessions)}", file=sys.stderr)

    print_message_stats(message_counts, skipped_short, args.min_messages)
    print_summary(runs)
    if args.csv:
        write_csv(args.csv, all_rows, requested_models)
        print(f"\nPer-session rows written to {args.csv}", file=sys.stderr)
    return 0


def print_message_stats(counts: list[int], skipped_short: int, min_messages: int) -> None:
    print()
    if not counts:
        print(f"No sessions with >= {min_messages} messages ({skipped_short} skipped for being too short).")
        return
    avg = sum(counts) / len(counts)
    print(f"Included sessions: {len(counts)} ({skipped_short} skipped for < {min_messages} messages)")
    print(f"Message count per session  avg={avg:.1f}  min={min(counts)}  max={max(counts)}")


def print_summary(runs: dict[str, ModelRun]) -> None:
    print()
    print(f"{'Model':<22}{'Sessions':>10}{'Input tok':>14}{'Output tok':>14}{'Total USD':>14}")
    print("-" * 74)
    for m, r in runs.items():
        if not r.sessions:
            continue
        print(f"{m:<22}{len(r.sessions):>10}{r.total_input:>14,}{r.total_output:>14,}{r.total_cost:>14,.4f}")


def write_csv(path: str, rows, models: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        header = ["experiment_id", "session_id", "messages"]
        for m in models:
            header += [f"{m}__input_tokens", f"{m}__output_tokens", f"{m}__usd"]
        w.writerow(header)
        for exp_id, sid, msg_count, per_model in rows:
            row = [exp_id, sid, msg_count]
            for m in models:
                c = per_model[m]
                row += [c.total_input_tokens, c.total_output_tokens, round(c.cost_usd, 6)]
            w.writerow(row)


if __name__ == "__main__":
    t0 = time.time()
    rc = run(build_arg_parser().parse_args())
    print(f"\nDone in {time.time() - t0:.1f}s", file=sys.stderr)
    sys.exit(rc)
