"""Shared value types for Swap Archeology.

Owned by the lead. Workers import from here and propose changes in their reports
rather than editing this module directly. Every quantity is an integer in the
token's smallest unit; no floats anywhere in the routing path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ChainId = int
Address = str  # lowercase 0x-prefixed hex, 20 bytes
HexBytes = str  # lowercase 0x-prefixed hex string


def norm_address(a: str) -> Address:
    a = a.lower()
    if not a.startswith("0x") or len(a) != 42:
        raise ValueError(f"bad address {a!r}")
    int(a, 16)
    return a


@dataclass(frozen=True)
class Token:
    chain: ChainId
    address: Address
    symbol: str
    decimals: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", norm_address(self.address))


NATIVE_ETH = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class BlockRef:
    """Identity of one historical block. Hash is mandatory: caches are keyed by it."""

    chain: ChainId
    number: int
    hash: HexBytes
    timestamp: int


@dataclass(frozen=True)
class CallSpec:
    """One eth_call identity. `tag` is an adapter-chosen label used to find the
    result again; it does not affect the cache key (chain, block hash, to, data)."""

    to: Address
    data: HexBytes
    tag: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", norm_address(self.to))
        object.__setattr__(self, "data", self.data.lower())


@dataclass(frozen=True)
class CallResult:
    spec: CallSpec
    success: bool
    raw: HexBytes  # return data on success, revert data on failure ("0x" if none)
    via: str = ""  # "multicall3" | "eth_call" | "cache"


class SupportStatus(str, Enum):
    SUPPORTED = "supported"  # discovered, state readable, quote semantics implemented + checked
    DISCOVERED_UNSUPPORTED = "discovered_unsupported"  # identity known, no trusted quote model yet
    UNRESOLVED = "unresolved"  # deployment/identity not yet pinned
    UNAVAILABLE = "unavailable"  # cannot be reconstructed from chain (e.g. RFQ offers)
    NOT_DEPLOYED_AT_BLOCK = "not_deployed_at_block"


@dataclass(frozen=True)
class PoolRecord:
    """A discovered venue. `pool_id` is globally unique within a chain:
    f"{family}:{deployment}:{pool}" where pool is an address or a bytes32 id."""

    family: str
    chain: ChainId
    pool_id: str
    deployment: Address  # factory / manager / registry / the contract itself for singletons
    pool: str  # pool address, or bytes32 pool id for singleton designs (V4, Balancer V3)
    tokens: tuple[Token, ...]
    config: Mapping[str, Any]  # fee, tick spacing, hooks, coin indexes, version...
    created_block: int | None  # None = unknown; never guess
    discovered_by: Mapping[str, Any]  # method, filter, block range, evidence links
    status: SupportStatus = SupportStatus.DISCOVERED_UNSUPPORTED
    notes: str = ""


@dataclass(frozen=True)
class TradeRequest:
    token_in: Token
    token_out: Token
    amount_in: int
    # Scoped opt-in for the zero-fee LitePSM DAI input lattice.  The evaluator
    # still verifies the actual PSM step and returns any permitted dust plainly.
    allow_psm_dai_refund: bool = False


@dataclass(frozen=True)
class Step:
    """One executed hop inside a plan. Steps are applied in list order."""

    pool_id: str
    token_in: Address
    token_out: Address
    amount_in: int


@dataclass(frozen=True)
class StepResult:
    step: Step
    amount_out: int
    fee_paid_in: int  # fee expressed in token_in units when the venue exposes it, else 0
    gas_estimate: int | None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """An ordered candidate produced by a solver. `solver` and `search_info` are
    provenance for the report; `steps` is the only thing the evaluator uses."""

    request: TradeRequest
    steps: tuple[Step, ...]
    solver: str
    search_info: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Evaluation:
    plan: Plan
    amount_out: int  # final token_out received, before gas
    amount_in_spent: int  # must equal request.amount_in for a full fill
    residual_in: int  # request.amount_in - amount_in_spent
    steps: tuple[StepResult, ...]
    feasible: bool
    reasons: tuple[str, ...] = ()  # why infeasible / partial / degraded
    gas_estimate: int | None = None
    terminal_refund: Mapping[Address, int] = field(default_factory=dict)
