"""Reconcile OCS's model catalogue and pricing seed against LiteLLM's price table.

The script is six layers, and ``main`` is those six calls in order::

    layer 1  ours   = read_default_models()            -> Catalogue (no rates)
    layer 2  ours   = with_pricing(ours, seed)         -> Catalogue (rates filled)
    layer 3  theirs = fetch() |> translate()           -> Catalogue
    layer 4  ledger = read_ledger()                    -> the pipeline's memory
    layer 5  diff   = compare(...)                     -> six lists
    layer 6  dispatch(reconciliation)                  -> seed, ledger, payload

Layer 5 yields one list per rule:

* ``added``       upstream has it, we do not, we never deleted it, and we have
  not rejected it. A model left undecided by an earlier run is still here, so
  nothing is stranded. Drives the catalogue PR.
* ``removed``     we list it, LiteLLM no longer offers it as a model OCS can
  run -- dropped from the price table, or still there but served only in some
  other mode. Drives the catalogue PR, which retires it.
* ``deprecated``  we still list it as active, its upstream deprecation date has
  passed. Drives the catalogue PR.
* ``repriced``    a rate we hold has moved upstream. Drives the pricing PR.
* ``backfilled``  a rate we lack and upstream has. Drives the pricing PR.
* ``unpriced``    still uncostable after backfill. Drives the missing-pricing issue.

Usage (from the repo root)::

    python3 -m scripts.auto_sync_models.run \\
        [--repo-root .] \\
        [--dry-run] \\
        [--today YYYY-MM-DD]    # deterministic-tests override

``reconciliation.json`` and ``reconciliation.pricing-body.md`` are written into
the repo root, where .gitignore already covers them.

Exit code is 1 when our own catalogue does not parse, or the price table cannot
be read, or reads back too small or too destructive to be believed; every signal
derives from those two, so a run without a trustworthy copy has nothing to say.
"""

from __future__ import annotations

import argparse
import datetime
import io
import sys
from decimal import Decimal
from pathlib import Path

from . import dispatch
from .catalogue import CatalogueUnreadable, load_seed, read_default_models, read_ledger, with_pricing
from .records import (
    REJECTED,
    REQUIRED_SERVICE_KINDS,
    Catalogue,
    Diff,
    Key,
    LedgerEntry,
    ModelRecord,
    PricingGap,
    RateChange,
    Reconciliation,
)
from .upstream import UpstreamUnavailable, fetch, translate

# The table carried 386 chat-capable models that mapped to an OCS provider when
# this floor was set. A structurally valid table translating to far fewer means
# LiteLLM changed shape -- a renamed ``litellm_provider``, a restructured key --
# not that upstream retired its catalogue.
MIN_UPSTREAM_MODELS = 300

# Providers retire models a few at a time. A run proposing to drop more than this
# share of what OCS serves has almost certainly lost a namespace rather than found
# a mass retirement.
MAX_REMOVED_FRACTION = 0.25


def check_upstream_size(all_chat: Catalogue) -> None:
    """Refuse a price table too small to have been read correctly."""
    if len(all_chat) < MIN_UPSTREAM_MODELS:
        raise UpstreamUnavailable(
            f"the LiteLLM price table translated to {len(all_chat)} model(s), under the floor of "
            f"{MIN_UPSTREAM_MODELS}; treating it as unreadable rather than as a mass retirement"
        )


def check_removal_scale(removed: list[ModelRecord], ours: Catalogue) -> None:
    """Refuse a comparison that would retire an implausible share of the catalogue."""
    if ours and len(removed) > len(ours) * MAX_REMOVED_FRACTION:
        raise UpstreamUnavailable(
            f"upstream would retire {len(removed)} of our {len(ours)} model(s), over the ceiling of "
            f"{MAX_REMOVED_FRACTION:.0%}; treating the table as unreadable rather than deleting them"
        )


def compare(
    ours: Catalogue,
    deleted: set[Key],
    live: Catalogue,
    all_chat: Catalogue,
    ledger: dict[Key, LedgerEntry],
) -> Diff:
    """The whole reconciliation: six lists off two catalogues and a ledger."""
    repriced, backfilled, unpriced = _compare_pricing(ours, all_chat)
    rejected = {key for key, entry in ledger.items() if entry.verdict == REJECTED}
    return Diff(
        added=[live[key] for key in sorted(live.keys() - ours.keys() - deleted - rejected)],
        removed=[ours[key] for key in sorted(ours.keys() - all_chat.keys())],
        deprecated=_newly_deprecated(ours, all_chat),
        repriced=repriced,
        backfilled=backfilled,
        unpriced=unpriced,
    )


def _newly_deprecated(ours: Catalogue, all_chat: Catalogue) -> list[ModelRecord]:
    """Models we still list as active whose upstream deprecation date has passed.

    The upstream record is returned, not ours: it carries the date.
    """
    return [
        all_chat[key]
        for key, record in sorted(ours.items())
        if not record.deprecated and key in all_chat and all_chat[key].deprecated
    ]


def _compare_pricing(
    ours: Catalogue,
    all_chat: Catalogue,
) -> tuple[list[RateChange], list[RateChange], list[PricingGap]]:
    """Split the rate comparison per service kind, not per model.

    A model with a seeded ``llm_input`` and no ``llm_output`` belongs in both
    lists: the input rate is compared, the output rate is filled. Splitting per
    model would drop partially-priced entries out of both.
    """
    repriced: list[RateChange] = []
    backfilled: list[RateChange] = []
    gaps: list[PricingGap] = []
    for (provider, model), record in sorted(ours.items()):
        upstream_rates = all_chat[(provider, model)].rates if (provider, model) in all_chat else {}
        for service_kind, new_price in sorted(upstream_rates.items()):
            old_price = record.rates.get(service_kind)
            change = RateChange(provider, model, service_kind, old_price, new_price)
            if old_price is None:
                backfilled.append(change)
            elif Decimal(old_price) != Decimal(new_price):
                repriced.append(change)
        costable = record.rates.keys() | upstream_rates.keys()
        missing = tuple(kind for kind in REQUIRED_SERVICE_KINDS if kind not in costable)
        if missing:
            gaps.append(PricingGap(provider, model, missing))
    return repriced, backfilled, gaps


def run(repo_root: Path, today: datetime.date) -> Reconciliation:
    """The six layers, minus dispatch. Returns what dispatch needs to write."""
    print("  Layer 1: reading the OCS model catalogue ...")
    ours, deleted = read_default_models(repo_root)
    print(f"  -> {len(ours)} registered model(s); {len(deleted)} deliberately deleted")

    print("  Layer 2: enriching with the pricing seed ...")
    ours, orphan_rows = with_pricing(ours, load_seed(repo_root))
    priced = sum(1 for record in ours.values() if record.rates)
    print(f"  -> {priced} priced; {len(orphan_rows)} seed row(s) with no catalogue entry")

    print("  Layer 3: fetching and translating the LiteLLM price table ...")
    live, all_chat = translate(fetch(), today)
    check_upstream_size(all_chat)
    print(f"  -> {len(all_chat)} chat-capable model(s), {len(live)} of them current")

    print("  Layer 4: reading the ledger ...")
    ledger = read_ledger(repo_root)
    print(f"  -> {len(ledger)} model(s) offered before")

    print("  Layer 5: comparing ...")
    diff = compare(ours=ours, deleted=deleted, live=live, all_chat=all_chat, ledger=ledger)
    check_removal_scale(removed=diff.removed, ours=ours)
    print(
        f"  -> {len(diff.added)} added, {len(diff.removed)} removed, {len(diff.deprecated)} newly deprecated, "
        f"{len(diff.repriced)} repriced, {len(diff.backfilled)} to backfill, {len(diff.unpriced)} uncostable"
    )
    return Reconciliation(diff=diff, orphan_rows=orphan_rows, ledger=ledger, today=today)


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    repo_root: Path = args.repo_root.resolve()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.datetime.now(datetime.UTC).date()
    # Unbuffered, so the CI log interleaves progress with any rate-limit sleeps
    # in real time rather than flushing everything at exit.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    print(f"[auto-sync-models] repo_root={repo_root} today={today}")

    try:
        reconciliation = run(repo_root, today)
    except (CatalogueUnreadable, UpstreamUnavailable) as exc:
        print(f"  (!) {exc}")
        return 1

    print("  Layer 6: dispatching ...")
    dispatch.dispatch(
        reconciliation=reconciliation,
        repo_root=repo_root,
        dry_run=args.dry_run,
    )
    return 0


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.auto_sync_models.run",
        description="Reconcile the OCS model catalogue and pricing seed against LiteLLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."), metavar="PATH")
    parser.add_argument("--today", help="YYYY-MM-DD override (for tests).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only: leave the pricing seed and the ledger untouched.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
