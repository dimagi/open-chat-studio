#!/usr/bin/env python3
"""Reconcile Dimagi's OpenAI + Anthropic account spend against OCS teams.

The OCS admin usage export only sees calls that go *through* OCS. The providers'
org-level cost reports see everything billed to the account. This script pulls
those cost reports (ground truth) and attributes each dollar back to an OCS team
using the key -> team mapping OCS exposes at ``/a/api/provider-keys/``.

Attribution model
-----------------
Neither provider bills per API key, so we can't read a per-key dollar figure:

* OpenAI  ``/organization/costs``          -> cost per (project_id, line_item)
* Anthropic ``/organizations/cost_report`` -> cost per workspace_id

But their *usage* reports (token counts) DO break down by api_key_id. So we:

1. Pull cost per project (OpenAI) / workspace (Anthropic).
2. Pull token usage per api_key_id within each project/workspace.
3. Split each project/workspace's cost across its keys by token share  <-- ESTIMATE.
4. Map api_key_id -> redacted key (from the provider key-list endpoint).
5. Map redacted key -> OCS team (last-4 match against OCS ``masked_key``).
6. Keys with no OCS match, and console usage (null api_key_id), fall into an
   "unattributed / non-OCS" bucket -- that is the spend OCS can't account for.

The per-team split in step 3 is proportional and therefore approximate; the
provider TOTALS printed at the end are exact. Treat team-level dollars as an
allocation, not an invoice.

Auth / config (env vars)
-------------------------
* OPENAI_ADMIN_KEY     admin key from platform.openai.com/settings/organization/admin-keys
* ANTHROPIC_ADMIN_KEY  admin key (sk-ant-admin...) from the Anthropic console
* OCS_BASE_URL         e.g. https://chatbots.dimagi.com
* OCS_SESSIONID        value of the `sessionid` cookie for a logged-in superuser
                       (these OCS endpoints are session-authed, like the admin UI)

Usage
-----
    pip install requests
    export OPENAI_ADMIN_KEY=... ANTHROPIC_ADMIN_KEY=... OCS_BASE_URL=... OCS_SESSIONID=...
    python provider_spend_reconcile.py --start 2026-06-01 --end 2026-06-30

Points to verify against current provider docs (marked VERIFY below): exact query
param names, pagination cursors, and the Anthropic cost `amount` unit (assumed cents).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import requests

OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 60
UNATTRIBUTED = "(unattributed / non-OCS)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def last4(masked: str) -> str | None:
    """Trailing 4 alphanumerics of a redacted key, the cross-provider join key.

    OCS ``masked_key`` and the providers' redacted values share the same tail,
    so the last 4 chars identify the key on both sides.
    """
    alnum = [c for c in (masked or "") if c.isalnum()]
    return "".join(alnum[-4:]) if len(alnum) >= 4 else None


def _to_unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def _to_rfc3339(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=UTC).isoformat().replace("+00:00", "Z")


@dataclass
class KeyUsage:
    """One provider API key: which team owns it and its token/cost allocation."""

    api_key_id: str
    scope_id: str  # openai project_id / anthropic workspace_id
    tokens: int = 0
    team: str | None = None
    cost: Decimal = Decimal(0)


@dataclass
class ProviderResult:
    name: str
    total_cost: Decimal = Decimal(0)
    per_team: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
class OpenAIClient:
    def __init__(self, admin_key: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {admin_key}"

    def _paginate(self, path: str, params: dict):
        """Yield result rows across cursor pages (org usage/costs style)."""
        params = dict(params)
        while True:
            resp = self.s.get(f"{OPENAI_BASE}{path}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            for bucket in payload.get("data", []):
                yield from bucket.get("results", [])
            if not payload.get("has_more"):
                return
            params["page"] = payload["next_page"]  # VERIFY: costs/usage use `page`/`next_page`

    def _list(self, path: str, params: dict | None = None):
        """Yield rows for object-list endpoints (projects, api_keys)."""
        params = dict(params or {})
        params.setdefault("limit", 100)
        while True:
            resp = self.s.get(f"{OPENAI_BASE}{path}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            yield from payload.get("data", [])
            if not payload.get("has_more"):
                return
            params["after"] = payload["last_id"]

    def redacted_by_key_id(self) -> dict[str, str]:
        """api_key_id -> redacted_value (e.g. ``sk-...JrYA``) across all projects."""
        out: dict[str, str] = {}
        for project in self._list("/organization/projects"):
            for key in self._list(f"/organization/projects/{project['id']}/api_keys"):
                out[key["id"]] = key.get("redacted_value", "")
        return out

    def cost_by_project(self, start: date, end: date) -> dict[str, Decimal]:
        params = {
            "start_time": _to_unix(start),
            "end_time": _to_unix(end),
            "bucket_width": "1d",
            "group_by": "project_id",
        }
        costs: dict[str, Decimal] = defaultdict(Decimal)
        for row in self._paginate("/organization/costs", params):
            amount = (row.get("amount") or {}).get("value", 0)  # USD dollars
            costs[row.get("project_id") or "default"] += Decimal(str(amount))
        return costs

    def tokens_by_key(self, start: date, end: date) -> list[KeyUsage]:
        params = {
            "start_time": _to_unix(start),
            "end_time": _to_unix(end),
            "bucket_width": "1d",
            "group_by": ["api_key_id", "project_id"],
        }
        agg: dict[tuple[str, str], KeyUsage] = {}
        for row in self._paginate("/organization/usage/completions", params):
            project = row.get("project_id") or "default"
            api_key_id = row.get("api_key_id")  # null => console / non-key usage
            tokens = (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
            key = (project, api_key_id or "")
            usage = agg.setdefault(key, KeyUsage(api_key_id=api_key_id or "", scope_id=project))
            usage.tokens += tokens
        return list(agg.values())


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicClient:
    def __init__(self, admin_key: str):
        self.s = requests.Session()
        self.s.headers.update({"x-api-key": admin_key, "anthropic-version": ANTHROPIC_VERSION})

    def _paginate_report(self, path: str, params: dict):
        params = dict(params)
        while True:
            resp = self.s.get(f"{ANTHROPIC_BASE}{path}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            for bucket in payload.get("data", []):
                yield from bucket.get("results", [])
            if not payload.get("has_more"):
                return
            params["page"] = payload["next_page"]

    def _list(self, path: str):
        params: dict = {"limit": 100}
        while True:
            resp = self.s.get(f"{ANTHROPIC_BASE}{path}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            yield from payload.get("data", [])
            if not payload.get("has_more"):
                return
            params["after_id"] = payload["last_id"]  # VERIFY: Anthropic list cursor param

    def hint_by_key_id(self) -> dict[str, str]:
        """api_key_id -> partial_key_hint (e.g. ``sk-ant-api03-cLV...lAAA``)."""
        return {k["id"]: k.get("partial_key_hint", "") for k in self._list("/organizations/api_keys")}

    def cost_by_workspace(self, start: date, end: date) -> dict[str, Decimal]:
        params = {"starting_at": _to_rfc3339(start), "ending_at": _to_rfc3339(end), "bucket_width": "1d"}
        params["group_by[]"] = "workspace_id"
        costs: dict[str, Decimal] = defaultdict(Decimal)
        for row in self._paginate_report("/organizations/cost_report", params):
            # VERIFY: docs describe `amount` in the currency's minor unit (cents) -> /100.
            amount = Decimal(str(row.get("amount", "0"))) / Decimal(100)
            costs[row.get("workspace_id") or "default"] += amount
        return costs

    def tokens_by_key(self, start: date, end: date) -> list[KeyUsage]:
        params = {"starting_at": _to_rfc3339(start), "ending_at": _to_rfc3339(end), "bucket_width": "1d", "limit": 31}
        params["group_by[]"] = ["api_key_id", "workspace_id"]
        agg: dict[tuple[str, str], KeyUsage] = {}
        for row in self._paginate_report("/organizations/usage_report/messages", params):
            workspace = row.get("workspace_id") or "default"
            api_key_id = row.get("api_key_id")
            cache = row.get("cache_creation") or {}
            tokens = (
                (row.get("uncached_input_tokens") or 0)
                + (row.get("cache_read_input_tokens") or 0)
                + (cache.get("ephemeral_1h_input_tokens") or 0)
                + (cache.get("ephemeral_5m_input_tokens") or 0)
                + (row.get("output_tokens") or 0)
            )
            key = (workspace, api_key_id or "")
            usage = agg.setdefault(key, KeyUsage(api_key_id=api_key_id or "", scope_id=workspace))
            usage.tokens += tokens
        return list(agg.values())


# --------------------------------------------------------------------------- #
# OCS
# --------------------------------------------------------------------------- #
class OCSClient:
    def __init__(self, base_url: str, sessionid: str):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.cookies.set("sessionid", sessionid)

    def team_by_last4(self, provider_prefix: str) -> dict[str, str]:
        """last-4 of masked_key -> team_name, for provider types starting with
        ``provider_prefix`` (``openai``/``azure`` or ``anthropic``).
        """
        resp = self.s.get(f"{self.base}/a/api/provider-keys/", timeout=TIMEOUT)
        resp.raise_for_status()
        mapping: dict[str, str] = {}
        for row in resp.json()["providers"]:
            if not row["provider_type"].startswith(provider_prefix):
                continue
            fp = last4(row.get("masked_key", ""))
            if fp:
                mapping[fp] = row["team_name"]
        return mapping

    def usage(self, start: date, end: date) -> dict:
        params = {"range_type": "custom", "start": start.isoformat(), "end": end.isoformat()}
        resp = self.s.get(f"{self.base}/a/api/provider-usage/", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #
def attribute(
    name: str,
    cost_by_scope: dict[str, Decimal],
    usages: list[KeyUsage],
    redacted_by_key_id: dict[str, str],
    team_by_last4: dict[str, str],
) -> ProviderResult:
    """Split each scope's cost across its keys by token share, then roll up per team."""
    result = ProviderResult(name=name, total_cost=sum(cost_by_scope.values(), Decimal(0)))

    tokens_per_scope: dict[str, int] = defaultdict(int)
    for u in usages:
        tokens_per_scope[u.scope_id] += u.tokens

    for u in usages:
        scope_cost = cost_by_scope.get(u.scope_id, Decimal(0))
        scope_tokens = tokens_per_scope[u.scope_id]
        u.cost = scope_cost * Decimal(u.tokens) / Decimal(scope_tokens) if scope_tokens else Decimal(0)

        team = None
        if u.api_key_id:
            fp = last4(redacted_by_key_id.get(u.api_key_id, ""))
            team = team_by_last4.get(fp) if fp else None
        result.per_team[team or UNATTRIBUTED] += u.cost

    # Cost in scopes that had cost but no usage rows (e.g. non-token line items)
    # can't be split by key; park it in the unattributed bucket so totals reconcile.
    attributed = sum(result.per_team.values(), Decimal(0))
    drift = result.total_cost - attributed
    if drift:
        result.per_team[UNATTRIBUTED] += drift
    return result


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def _money(d: Decimal) -> str:
    return f"${d.quantize(Decimal('0.01'))}"


def print_report(openai: ProviderResult, anthropic: ProviderResult, ocs_usage: dict) -> None:
    teams = sorted(set(openai.per_team) | set(anthropic.per_team))
    ocs_by_name = {t["team_name"]: t for t in ocs_usage.get("teams", [])}

    width = max((len(t) for t in teams), default=10)
    header = f"{'Team':<{width}}  {'OpenAI':>12}  {'Anthropic':>12}  {'Total':>12}  {'OCS tokens':>12}  cost_tracking"
    print("\n" + header)
    print("-" * len(header))
    grand = Decimal(0)
    for team in teams:
        oa = openai.per_team.get(team, Decimal(0))
        an = anthropic.per_team.get(team, Decimal(0))
        total = oa + an
        grand += total
        ocs = ocs_by_name.get(team, {})
        tokens = ocs.get("total_tokens", "" if team == UNATTRIBUTED else 0)
        flag = "" if team == UNATTRIBUTED else ("yes" if ocs.get("cost_tracking_enabled") else "no")
        print(f"{team:<{width}}  {_money(oa):>12}  {_money(an):>12}  {_money(total):>12}  {str(tokens):>12}  {flag}")

    print("-" * len(header))
    provider_total = openai.total_cost + anthropic.total_cost
    print(
        f"{'PROVIDER TOTALS':<{width}}  {_money(openai.total_cost):>12}  "
        f"{_money(anthropic.total_cost):>12}  {_money(provider_total):>12}"
    )

    unattributed = openai.per_team.get(UNATTRIBUTED, Decimal(0)) + anthropic.per_team.get(UNATTRIBUTED, Decimal(0))
    print(f"\nAttributed to a team: {_money(grand - unattributed)}")
    print(f"Non-OCS / unattributed: {_money(unattributed)}")
    print("\nNote: per-team dollars are a token-share allocation of project/workspace cost;")
    print("provider totals are exact.")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required env var: {name}")
    return value


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=today - timedelta(days=30))
    parser.add_argument("--end", type=date.fromisoformat, default=today)
    args = parser.parse_args()

    openai_client = OpenAIClient(_require_env("OPENAI_ADMIN_KEY"))
    anthropic_client = AnthropicClient(_require_env("ANTHROPIC_ADMIN_KEY"))
    ocs = OCSClient(_require_env("OCS_BASE_URL"), _require_env("OCS_SESSIONID"))

    print(f"Reconciling {args.start} .. {args.end}", file=sys.stderr)

    openai_result = attribute(
        "openai",
        openai_client.cost_by_project(args.start, args.end),
        openai_client.tokens_by_key(args.start, args.end),
        openai_client.redacted_by_key_id(),
        ocs.team_by_last4("openai"),  # 'openai' also matches 'azure'? adjust if Azure billed here
    )
    anthropic_result = attribute(
        "anthropic",
        anthropic_client.cost_by_workspace(args.start, args.end),
        anthropic_client.tokens_by_key(args.start, args.end),
        anthropic_client.hint_by_key_id(),
        ocs.team_by_last4("anthropic"),
    )

    print_report(openai_result, anthropic_result, ocs.usage(args.start, args.end))


if __name__ == "__main__":
    main()
