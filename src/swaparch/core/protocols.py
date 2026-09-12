"""Shared behavioural interfaces. Owned by the lead.

Layering (nothing calls upward):

  rpc  ->  snapshot  ->  adapters (state + quote)  ->  evaluator  ->  solver/report

* `rpc` is the only module that talks to a node.
* `Snapshot` is an immutable, block-hash-pinned bag of CallResults.
* An adapter turns a PoolRecord + Snapshot into a `PoolState` that quotes purely
  in memory. Adapters never perform RPC; they *describe* the reads they need.
* `PoolState.swap()` returns a NEW state so shared-capacity effects are explicit.
* Solvers only see PoolStates and a TradeRequest; the evaluator re-executes the
  plan in order against fresh copies of the states and is the single source of
  truth for the reported number.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .types import (
    Address,
    BlockRef,
    CallResult,
    CallSpec,
    Evaluation,
    Plan,
    PoolRecord,
    Token,
    TradeRequest,
)


@runtime_checkable
class Snapshot(Protocol):
    block: BlockRef

    def get(self, spec: CallSpec) -> CallResult: ...
    def has(self, spec: CallSpec) -> bool: ...


class Unsupported(Exception):
    """Raised by an adapter when a discovered pool cannot be priced at this block.
    The message is surfaced in reports; it must say *why*."""


@runtime_checkable
class PoolState(Protocol):
    """Immutable per-block view of one venue. All methods are pure and integer exact."""

    record: PoolRecord

    def tokens(self) -> tuple[Token, ...]: ...

    def capacity_ids(self) -> tuple[str, ...]:
        """Shared-resource identities this venue draws on (its own pool_id, a Curve
        base pool, an ERC-4626 vault, a Balancer buffer...). The evaluator refuses
        plans that would consume one capacity id from two states that do not share
        the same object."""
        ...

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        """amount_out for exact input against *this* state, no mutation.
        Raises Unsupported if direction/size cannot be priced."""
        ...

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, PoolState]:
        """(amount_out, new_state). new_state reflects the executed trade."""
        ...

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None: ...


@runtime_checkable
class SourceAdapter(Protocol):
    family: str

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        """Independent reads for phase 1 (batched in one Multicall by the acquirer)."""
        ...

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        """Additional reads that depend on phase-1 results (tick words, coin lists...).
        The acquirer loops until this returns an empty list. Default: []."""
        ...

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> PoolState:
        """Build a PoolState from the snapshot. Raise Unsupported with a reason."""
        ...


@runtime_checkable
class Solver(Protocol):
    name: str

    def solve(self, request: TradeRequest, states: Iterable[PoolState]) -> list[Plan]:
        """Return candidate plans, best first by the solver's own estimate.
        Must always include the best single-venue direct plan when one exists,
        so the evaluator can never report a split that is worse than no split."""
        ...


class Evaluator(Protocol):
    def evaluate(self, plan: Plan, states: Iterable[PoolState]) -> Evaluation: ...
