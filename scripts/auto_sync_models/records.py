"""The shapes both sides of the reconciliation produce, and what comparing them yields.

Our catalogue and LiteLLM's price table are read into the same ``ModelRecord``
mapping, keyed by ``(provider, model_name)``, so the comparison in ``sync`` is
set algebra rather than per-model probing.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

# (provider, model_name)
Key = tuple[str, str]

PENDING = "pending"
REGISTERED = "registered"
REJECTED = "rejected"

# A model needs both to be costable; a cached-input rate alone is not useful.
REQUIRED_SERVICE_KINDS = ("llm_input", "llm_output")


@dataclass(frozen=True)
class ModelRecord:
    """One model as offered by one provider.

    ``deprecation_date`` and ``source_key`` are only ever set on the upstream
    side, ``replacement`` only on ours. The shared key space is the point: it is
    what lets the two mappings be subtracted from each other.
    """

    provider: str
    name: str
    token_limit: int | None = None
    rates: dict[str, str] = field(default_factory=dict)
    deprecated: bool = False
    deprecation_date: str | None = None
    replacement: str | None = None
    source_key: str | None = None
    params: dict = field(default_factory=dict)

    @property
    def key(self) -> Key:
        return (self.provider, self.name)

    def with_rates(self, rates: dict[str, str]) -> ModelRecord:
        return dataclasses.replace(self, rates=rates)


Catalogue = dict[Key, ModelRecord]


@dataclass(frozen=True)
class LedgerEntry:
    """One model the pipeline has offered, and what was decided about it."""

    provider: str
    model: str
    first_seen: str
    verdict: str = PENDING
    reason: str | None = None
    params: dict = field(default_factory=dict)

    @property
    def key(self) -> Key:
        return (self.provider, self.model)


@dataclass(frozen=True)
class RateChange:
    """One service kind's price moving, or arriving where we had none."""

    provider: str
    model: str
    service_kind: str
    old_price: str | None
    new_price: str

    @property
    def key(self) -> Key:
        return (self.provider, self.model)


@dataclass(frozen=True)
class PricingGap:
    """An active model the seed cannot cost, and which kinds it is missing."""

    provider: str
    model: str
    kinds_missing: tuple[str, ...]


@dataclass(frozen=True)
class Diff:
    """Everything one comparison yields. Each field is one rule; see ``sync.compare``."""

    added: list[ModelRecord] = field(default_factory=list)
    removed: list[ModelRecord] = field(default_factory=list)
    deprecated: list[ModelRecord] = field(default_factory=list)
    repriced: list[RateChange] = field(default_factory=list)
    backfilled: list[RateChange] = field(default_factory=list)
    unpriced: list[PricingGap] = field(default_factory=list)

    @property
    def has_pricing_work(self) -> bool:
        return bool(self.repriced or self.backfilled)

    @property
    def has_catalogue_work(self) -> bool:
        return bool(self.added or self.deprecated or self.removed)

    @property
    def has_work(self) -> bool:
        """Whether this run is worth an agent. ``unpriced`` is reported, never acted on."""
        return self.has_catalogue_work or self.has_pricing_work


@dataclass(frozen=True)
class Reconciliation:
    """What layers 1-5 produced, and the date they were produced against."""

    diff: Diff
    orphan_rows: list[dict]
    ledger: dict[Key, LedgerEntry]
    today: datetime.date
