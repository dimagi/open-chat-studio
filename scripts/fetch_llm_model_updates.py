#!/usr/bin/env python
"""Fetch recent LLM model releases from llm-stats.com (via the zeroeval Stats API).

Filters the results to organizations that map 1:1 to Open Chat Studio providers
(openai, anthropic, google, deepseek, perplexity) and enriches each match with
the detail endpoint payload. Writes the result to a JSON file for downstream
consumption (e.g. the auto-update-models workflow, or local inspection).

Usage:
    LLM_STATS_BEARER_TOKEN=... python scripts/fetch_llm_model_updates.py
    LLM_STATS_BEARER_TOKEN=... python scripts/fetch_llm_model_updates.py --days 7
    LLM_STATS_BEARER_TOKEN=... python scripts/fetch_llm_model_updates.py --output /tmp/models.json

When run from a GitHub Actions step, GITHUB_OUTPUT is automatically populated
with `has_models`, `model_count`, and `model_ids` if the env var is set.

Exit codes:
    0 — succeeded (regardless of whether matches were found)
    1 — API error or missing bearer token
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Organizations on llm-stats.com that map to OCS default_models providers.
# Strict match — other orgs (xai, mistral, qwen, moonshotai, etc.) are ignored.
OCS_ORGS = {"openai", "anthropic", "google", "deepseek", "perplexity"}

# Fields on the detail response that are noisy and not useful for adding
# a model to default_models.py — drop them to keep the JSON lean.
NOISY_DETAIL_FIELDS = {"scores", "top_scores", "providers"}

UPDATES_URL = "https://api.zeroeval.com/stats/v1/updates"
MODEL_DETAIL_URL = "https://api.zeroeval.com/stats/v1/models/{model_id}"


def api_get(url: str, bearer: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "ocs-auto-models-workflow/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_matched_models(bearer: str, days: int, limit: int = 30) -> list[dict]:
    updates = api_get(f"{UPDATES_URL}?days={days}&limit={limit}", bearer)
    matched = [
        m
        for m in updates.get("models", [])
        if m.get("organization", {}).get("id") in OCS_ORGS and m.get("model_type") == "llm"
    ]

    enriched = []
    for m in matched:
        model_id = m["id"]
        try:
            details = api_get(MODEL_DETAIL_URL.format(model_id=model_id), bearer)
            for field in NOISY_DETAIL_FIELDS:
                details.pop(field, None)
            m["details"] = details
            if details.get("context_window") and not m.get("context_window"):
                m["context_window"] = details["context_window"]
        except urllib.error.HTTPError as e:
            m["details_error"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            m["details_error"] = str(e)
        enriched.append(m)

    return enriched


def write_github_output(enriched: list[dict]) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    model_ids = ",".join(m["id"] for m in enriched)
    with open(gh_out, "a") as f:
        f.write(f"has_models={'true' if enriched else 'false'}\n")
        f.write(f"model_count={len(enriched)}\n")
        f.write(f"model_ids={model_ids}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.environ.get("DAYS", "1")),
        help="Lookback window in days (default: 1, or $DAYS env var)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max number of update entries to request (default: 30)",
    )
    parser.add_argument(
        "--output",
        default="matched_models.json",
        help="Path to write the JSON payload (default: matched_models.json)",
    )
    args = parser.parse_args()

    bearer = os.environ.get("LLM_STATS_BEARER_TOKEN")
    if not bearer:
        print("ERROR: LLM_STATS_BEARER_TOKEN environment variable is required.", file=sys.stderr)
        return 1

    try:
        enriched = fetch_matched_models(bearer, days=args.days, limit=args.limit)
    except urllib.error.HTTPError as e:
        print(f"ERROR: API request failed: HTTP {e.code} {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"ERROR: API request failed: {e.reason}", file=sys.stderr)
        return 1

    with open(args.output, "w") as f:
        json.dump(enriched, f, indent=2)

    write_github_output(enriched)

    print(f"Found {len(enriched)} candidate model(s) from OCS providers")
    for m in enriched:
        org = m.get("organization", {}).get("id", "?")
        ctx = m.get("context_window") or "unknown"
        print(f"  - [{org}] {m['id']} (context_window={ctx})")
    print(f"Wrote payload to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
