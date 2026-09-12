"""Read-only Bebop adapter and RFQ quote normalization.

Bebop executes trades via off-chain RFQ/PMM market maker quotes settled on-chain
through BebopSettlement (Blend) or BebopRouter.

Historical off-chain quotes are not recorded on-chain; on-chain settlement logs
only record executed trades, never counterfactual offered liquidity or depth.
Therefore, historical replay strictly requires an external quote archive and
refuses to substitute live/current quotes at historical block pins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ..core.protocols import PoolState, Snapshot, Unsupported
from ..core.types import (
    Address,
    BlockRef,
    CallSpec,
    PoolRecord,
    Token,
    norm_address,
)

# Canonical Ethereum Mainnet Deployments
SETTLEMENT_BLEND = "0xbbbbbbb520d69a9775e85b458c58c648259fad5f"  # Current PMM/RFQ settlement
SETTLEMENT_LEGACY_V2 = "0xbeb09000fa59627dc02bb55448ac1893eaa501a5"  # Legacy PMM/RFQ settlement
SETTLEMENT_JAM = "0xbeb0b0623f66be8ce162ebdfa2ec543a522f4ea6"  # Current JAM/solver settlement
SETTLEMENT_JAM_OLD = "0xbebebeb035351f58602e0c1c8b59ecbff5d5f47b"  # Old JAM settlement
ROUTER_CURRENT = "0xbeb0009aca35087ce7ccf11637e24dd1aad3bf2a"  # Current RFQ router entrypoint
JAM_BALANCE_MANAGER = "0xc5a350853e4e36b73eb0c24aaa4b8816c9a3579a"

KNOWN_SETTLEMENT_CONTRACTS = {
    SETTLEMENT_BLEND,
    SETTLEMENT_LEGACY_V2,
    SETTLEMENT_JAM,
    SETTLEMENT_JAM_OLD,
    ROUTER_CURRENT,
}


@dataclass(frozen=True)
class BebopQuote:
    """Normalized Bebop RFQ quote."""

    quote_id: str
    chain_id: int
    taker_address: Address
    receiver_address: Address
    token_in: Token
    token_out: Token
    amount_in: int
    amount_out: int
    expiry: int  # Unix timestamp seconds
    approval_target: Address
    settlement_address: Address
    status: str
    tx_to: Address | None
    tx_data: str | None
    gas_estimate: int | None
    gas_price: int | None
    price_impact: float | None
    fetch_time: str
    is_historical: bool
    is_demo: bool
    raw_response: Mapping[str, Any] = field(default_factory=dict)


def normalize_bebop_quote(
    raw: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
    is_historical: bool = False,
    reference_timestamp: int | None = None,
) -> BebopQuote:
    """Ingest and validate a raw Bebop RFQ quote (GET /pmm/{chain}/v3/quote)."""
    status = raw.get("status")
    if status not in ("SIG_SUCCESS", "Success"):
        raise ValueError(f"Bebop quote status {status!r} is not executable")

    quote_id = raw.get("quoteId")
    if not quote_id or not isinstance(quote_id, str):
        raise ValueError("Missing or invalid quoteId in Bebop quote")

    chain_id = raw.get("chainId")
    if chain_id != 1:
        raise ValueError(f"Unsupported chainId {chain_id}; expected 1 (Ethereum)")

    expiry = raw.get("expiry")
    if not isinstance(expiry, int) or expiry <= 0:
        raise ValueError(f"Invalid expiry {expiry!r} in Bebop quote")

    if reference_timestamp is not None and reference_timestamp >= expiry:
        raise ValueError(
            f"Quote expired: reference timestamp {reference_timestamp} >= expiry {expiry}"
        )

    taker_raw = raw.get("taker")
    if not taker_raw or not isinstance(taker_raw, str):
        raise ValueError("Missing taker address in Bebop quote")
    taker_address = norm_address(taker_raw)

    receiver_raw = raw.get("receiver") or taker_raw
    receiver_address = norm_address(receiver_raw)

    approval_raw = raw.get("approvalTarget")
    if not approval_raw or not isinstance(approval_raw, str):
        raise ValueError("Missing approvalTarget in Bebop quote")
    approval_target = norm_address(approval_raw)

    settlement_raw = raw.get("settlementAddress")
    if not settlement_raw or not isinstance(settlement_raw, str):
        raise ValueError("Missing settlementAddress in Bebop quote")
    settlement_address = norm_address(settlement_raw)

    sell_tokens = raw.get("sellTokens", {})
    buy_tokens = raw.get("buyTokens", {})

    if len(sell_tokens) != 1 or len(buy_tokens) != 1:
        raise ValueError("Bebop 1-to-1 quote must contain exactly one sell and one buy token")

    sell_addr_raw, sell_info = next(iter(sell_tokens.items()))
    buy_addr_raw, buy_info = next(iter(buy_tokens.items()))

    sell_addr = norm_address(sell_addr_raw)
    buy_addr = norm_address(buy_addr_raw)

    try:
        amount_in = int(sell_info["amount"])
        decimals_in = int(sell_info.get("decimals", 18))
        amount_out = int(buy_info["amount"])
        decimals_out = int(buy_info.get("decimals", 6))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Malformed token amount or decimals in Bebop quote: {exc}") from exc

    if amount_in <= 0 or amount_out <= 0:
        raise ValueError(f"Amounts must be strictly positive (in={amount_in}, out={amount_out})")

    token_in = Token(chain_id, sell_addr, sell_info.get("symbol", ""), decimals_in)
    token_out = Token(chain_id, buy_addr, buy_info.get("symbol", ""), decimals_out)

    tx = raw.get("tx") or {}
    tx_to = norm_address(tx["to"]) if tx.get("to") else None
    tx_data = tx.get("data")
    gas_estimate = int(tx["gas"]) if tx.get("gas") is not None else None
    gas_price = int(tx["gasPrice"]) if tx.get("gasPrice") is not None else None

    price_impact = raw.get("priceImpact")
    if price_impact is not None:
        try:
            price_impact = float(price_impact)
        except (TypeError, ValueError):
            price_impact = None

    info_text = str(raw.get("info", "")).lower()
    is_demo = "public api" in info_text or "demo" in info_text

    fetch_time = fetched_at or datetime.now(UTC).isoformat()

    return BebopQuote(
        quote_id=quote_id,
        chain_id=chain_id,
        taker_address=taker_address,
        receiver_address=receiver_address,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=amount_out,
        expiry=expiry,
        approval_target=approval_target,
        settlement_address=settlement_address,
        status=status,
        tx_to=tx_to,
        tx_data=tx_data,
        gas_estimate=gas_estimate,
        gas_price=gas_price,
        price_impact=price_impact,
        fetch_time=fetch_time,
        is_historical=is_historical,
        is_demo=is_demo,
        raw_response=dict(raw),
    )


def validate_quote_for_historical_block(quote: BebopQuote, block: BlockRef) -> None:
    """Enforce refusal to promote current quotes to pinned historical blocks."""
    if not quote.is_historical:
        raise Unsupported(
            f"requires_external_quote_archive: refusing live/current quote at historical "
            f"block {block.number} (fetch_time={quote.fetch_time}, "
            f"block_timestamp={block.timestamp})"
        )
    if block.timestamp >= quote.expiry:
        raise Unsupported(
            f"historical quote expired: block timestamp {block.timestamp} >= expiry {quote.expiry}"
        )


@dataclass(frozen=True)
class BebopPoolState:
    """Immutable quote state for a specific discrete Bebop RFQ offer.

    RFQ offers are discrete and non-interpolable: a quote for size X at price P
    cannot be scaled to size Y or split arbitrarily without market maker pricing.
    """

    record: PoolRecord
    quote: BebopQuote
    consumed: bool = False

    def tokens(self) -> tuple[Token, ...]:
        return (self.quote.token_in, self.quote.token_out)

    def capacity_ids(self) -> tuple[str, ...]:
        return (f"bebop:{self.quote.quote_id}",)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if self.consumed:
            raise Unsupported("Bebop RFQ quote already consumed (single-use discrete quote)")

        tin = norm_address(token_in)
        tout = norm_address(token_out)

        if tin != self.quote.token_in.address or tout != self.quote.token_out.address:
            raise Unsupported(
                f"direction/token mismatch: quote is {self.quote.token_in.address} -> "
                f"{self.quote.token_out.address}, requested {tin} -> {tout}"
            )

        if amount_in != self.quote.amount_in:
            raise Unsupported(
                f"Bebop RFQ quote is discrete for exact amount {self.quote.amount_in}; "
                f"cannot interpolate or scale to {amount_in}"
            )

        if self.quote.status not in ("SIG_SUCCESS", "Success"):
            raise Unsupported(f"Bebop quote status {self.quote.status} is not executable")

        return self.quote.amount_out

    def swap(self, token_in: Address, token_out: Address, amount_in: int) -> tuple[int, BebopPoolState]:
        amount_out = self.quote_exact_in(token_in, token_out, amount_in)
        return amount_out, replace(self, consumed=True)

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        return self.quote.gas_estimate


class BebopAdapter:
    """Read-only adapter for Bebop PMM/RFQ."""

    family = "bebop"

    def read_requests(self, pool: PoolRecord, block: BlockRef) -> list[CallSpec]:
        """RFQ offers are offchain; no on-chain state queries are issued for quoting."""
        return []

    def dependent_requests(
        self, pool: PoolRecord, block: BlockRef, snapshot: Snapshot
    ) -> list[CallSpec]:
        return []

    def load_state(self, pool: PoolRecord, snapshot: Snapshot) -> PoolState:
        """Historical blocks without archived external quote cannot be reconstructed."""
        raise Unsupported(
            f"requires_external_quote_archive: Bebop RFQ offers are offchain; "
            f"historical fills do not reconstruct counterfactual available prices or capacity "
            f"at block {snapshot.block.number}"
        )

    def load_state_from_quote(
        self,
        pool: PoolRecord,
        quote: BebopQuote,
        block: BlockRef | None = None,
    ) -> BebopPoolState:
        """Construct a PoolState from a validated external quote."""
        if block is not None:
            validate_quote_for_historical_block(quote, block)
        return BebopPoolState(record=pool, quote=quote)
