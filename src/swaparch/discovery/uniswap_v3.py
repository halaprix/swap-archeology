"""Uniswap V3 pool discovery: `PoolCreated` log filters and their decoder.

Identity
--------
``UniswapV3Factory`` on Ethereum mainnet is
``0x1F98431c8aD98523631AE4a59f267346ea31F984``.  Its creation block is recorded
in :data:`FACTORY_CREATION_BLOCK` and verified by evidence under
``data/adapters-evidence/uniswap_v3/`` (``eth_getCode`` empty at
``FACTORY_CREATION_BLOCK - 1`` and non-empty at ``FACTORY_CREATION_BLOCK``, plus
the block number of the earliest ``PoolCreated`` log).

Events
------
::

    PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee,
                int24 tickSpacing, address pool)
    FeeAmountEnabled(uint24 indexed fee, int24 indexed tickSpacing)

``PoolCreated`` has three indexed parameters, so a pair-filtered query pins
``topic1 = token0`` and ``topic2 = token1`` and leaves ``topic3`` (the fee tier)
unset - every fee tier for the pair comes back in one query, including tiers
outside the familiar 100/500/3000/10000 set.

The factory itself sorts the pair (``require(tokenA != tokenB); (token0, token1)
= tokenA < tokenB ? ...``), so for an unordered pair only the numerically sorted
ordering can ever match.  :func:`build_pool_created_filters` still emits both
orderings - each carries ``canonical: bool`` - so a caller can prove the reverse
ordering is empty rather than assume it.

Coverage
--------
A pair-filtered scan covers exactly the pairs it names.  Adding an endpoint token
later requires backfilling the new pairs from :data:`FACTORY_CREATION_BLOCK`; the
inventory's ``coverage`` list is what makes that checkable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from eth_utils import keccak

from ..core.types import Address, PoolRecord, SupportStatus, Token, norm_address

FACTORY: Address = "0x1f98431c8ad98523631ae4a59f267346ea31f984"

#: Block containing the factory's deployment transaction.  Verified on chain; see
#: ``data/adapters-evidence/uniswap_v3/factory-creation.json``.
FACTORY_CREATION_BLOCK = 12369621

POOL_CREATED_SIG = "PoolCreated(address,address,uint24,int24,address)"
FEE_AMOUNT_ENABLED_SIG = "FeeAmountEnabled(uint24,int24)"

TOPIC_POOL_CREATED = "0x" + keccak(text=POOL_CREATED_SIG).hex()
TOPIC_FEE_AMOUNT_ENABLED = "0x" + keccak(text=FEE_AMOUNT_ENABLED_SIG).hex()

#: Study endpoints (BRIEF.md) plus the two connectors this family needs.
ENDPOINT_TOKENS: Mapping[str, Address] = {
    "WETH": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "DAI": "0x6b175474e89094c44da98b954eedeac495271d0f",
    "wstETH": "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0",
    "sUSDe": "0x9d39a5de30e57443bff2a8307a4256c8797a3497",
}
CONNECTOR_TOKENS: Mapping[str, Address] = {
    "stETH": "0xae7ab96520de3a18e5e111b5eaab095312d7fe84",
    "USDe": "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",
}
DISCOVERY_TOKENS: Mapping[str, Address] = {**ENDPOINT_TOKENS, **CONNECTOR_TOKENS}


def _topic_address(address: str) -> str:
    return "0x" + norm_address(address)[2:].rjust(64, "0")


@dataclass(frozen=True)
class LogFilterSpec:
    """One ``eth_getLogs`` request identity (also what ``cast logs`` takes)."""

    address: Address
    topics: tuple[str | None, ...]
    from_block: int
    to_block: int
    meta: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "topics": list(self.topics),
            "fromBlock": hex(self.from_block),
            "toBlock": hex(self.to_block),
            "meta": dict(self.meta),
        }

    def cast_args(self) -> list[str]:
        """Positional topic arguments for ``cast logs`` (``null`` for a wildcard)."""
        return [t if t is not None else "null" for t in self.topics]


def unordered_pairs(addresses: Sequence[Address]) -> list[tuple[Address, Address]]:
    out = []
    for i, a in enumerate(addresses):
        for b in addresses[i + 1:]:
            out.append((a, b))
    return out


def build_pool_created_filters(
    tokens: Mapping[str, Address] | Iterable[Address],
    from_block: int = FACTORY_CREATION_BLOCK,
    to_block: int | None = None,
    factory: Address = FACTORY,
) -> list[LogFilterSpec]:
    """One filter per *ordered* pair of the given tokens.

    ``tokens`` may be a ``{symbol: address}`` mapping or a bare address iterable.
    ``topic3`` (fee) is deliberately left unset so unusual fee tiers are found.
    Each spec's ``meta`` carries the symbols, the ordering and whether that
    ordering is the one the factory can actually have written
    (``token0 < token1``).
    """
    if isinstance(tokens, Mapping):
        symbols = {norm_address(v): k for k, v in tokens.items()}
        addresses = [norm_address(v) for v in tokens.values()]
    else:
        addresses = [norm_address(a) for a in tokens]
        symbols = {a: a for a in addresses}
    if to_block is None:
        raise ValueError("to_block must be pinned so coverage is checkable")

    specs: list[LogFilterSpec] = []
    for a, b in unordered_pairs(addresses):
        for t0, t1 in ((a, b), (b, a)):
            specs.append(
                LogFilterSpec(
                    address=norm_address(factory),
                    topics=(TOPIC_POOL_CREATED, _topic_address(t0), _topic_address(t1)),
                    from_block=from_block,
                    to_block=to_block,
                    meta={
                        "event": "PoolCreated",
                        "token0": t0,
                        "token1": t1,
                        "token0_symbol": symbols[t0],
                        "token1_symbol": symbols[t1],
                        "canonical": int(t0, 16) < int(t1, 16),
                    },
                )
            )
    return specs


def build_fee_amount_enabled_filter(
    from_block: int = FACTORY_CREATION_BLOCK,
    to_block: int | None = None,
    factory: Address = FACTORY,
) -> LogFilterSpec:
    if to_block is None:
        raise ValueError("to_block must be pinned so coverage is checkable")
    return LogFilterSpec(
        address=norm_address(factory),
        topics=(TOPIC_FEE_AMOUNT_ENABLED,),
        from_block=from_block,
        to_block=to_block,
        meta={"event": "FeeAmountEnabled"},
    )


# ------------------------------------------------------------------ decoding


def _int24_from_word(word_hex: str) -> int:
    value = int(word_hex, 16)
    # int24 is left-padded with sign bits to 32 bytes
    if value >= 1 << 255:
        value -= 1 << 256
    return value


def _block_number(log: Mapping[str, Any]) -> int | None:
    raw = log.get("blockNumber")
    if raw is None:
        return None
    return int(raw, 16) if isinstance(raw, str) else int(raw)


def decode_pool_created(log: Mapping[str, Any]) -> dict[str, Any]:
    """Decode one raw ``PoolCreated`` log (eth_getLogs / ``cast logs --json`` shape)."""
    topics = [t.lower() for t in log["topics"]]
    if topics[0] != TOPIC_POOL_CREATED:
        raise ValueError(f"not a PoolCreated log: topic0 {topics[0]}")
    data = log["data"].removeprefix("0x")
    if len(data) != 128:
        raise ValueError(f"PoolCreated data must be 2 words, got {len(data)//2} bytes")
    return {
        "token0": norm_address("0x" + topics[1][-40:]),
        "token1": norm_address("0x" + topics[2][-40:]),
        "fee": int(topics[3], 16),
        "tick_spacing": _int24_from_word(data[0:64]),
        "pool": norm_address("0x" + data[64:128][-40:]),
        "created_block": _block_number(log),
        "tx_hash": log.get("transactionHash"),
        "log_index": (
            int(log["logIndex"], 16) if isinstance(log.get("logIndex"), str)
            else log.get("logIndex")
        ),
    }


def decode_fee_amount_enabled(log: Mapping[str, Any]) -> dict[str, Any]:
    topics = [t.lower() for t in log["topics"]]
    if topics[0] != TOPIC_FEE_AMOUNT_ENABLED:
        raise ValueError(f"not a FeeAmountEnabled log: topic0 {topics[0]}")
    return {
        "fee": int(topics[1], 16),
        "tick_spacing": _int24_from_word(topics[2][2:]),
        "enabled_block": _block_number(log),
        "tx_hash": log.get("transactionHash"),
    }


def pool_id(pool: Address, factory: Address = FACTORY) -> str:
    return f"uniswap_v3:{norm_address(factory)}:{norm_address(pool)}"


def pool_record_from_log(
    log: Mapping[str, Any],
    tokens: Mapping[Address, Token],
    chain: int = 1,
    factory: Address = FACTORY,
    filter_meta: Mapping[str, Any] | None = None,
    status: SupportStatus = SupportStatus.DISCOVERED_UNSUPPORTED,
) -> PoolRecord:
    """Turn one decoded ``PoolCreated`` log into a :class:`PoolRecord`.

    ``tokens`` must supply resolved ``Token`` values (symbol + decimals) for both
    sides; discovery never guesses decimals.
    """
    d = decode_pool_created(log)
    try:
        t0, t1 = tokens[d["token0"]], tokens[d["token1"]]
    except KeyError as exc:  # pragma: no cover - caller controls the token map
        raise KeyError(f"no resolved Token for {exc.args[0]}") from None
    return PoolRecord(
        family="uniswap_v3",
        chain=chain,
        pool_id=pool_id(d["pool"], factory),
        deployment=norm_address(factory),
        pool=d["pool"],
        tokens=(t0, t1),
        config={"fee": d["fee"], "tick_spacing": d["tick_spacing"]},
        created_block=d["created_block"],
        discovered_by={
            "method": "logs:PoolCreated",
            "deployment": norm_address(factory),
            "tx_hash": d["tx_hash"],
            "log_index": d["log_index"],
            **({"filter": dict(filter_meta)} if filter_meta else {}),
        },
        status=status,
    )


def pool_record_to_json(record: PoolRecord) -> dict[str, Any]:
    return {
        "family": record.family,
        "chain": record.chain,
        "pool_id": record.pool_id,
        "deployment": record.deployment,
        "pool": record.pool,
        "tokens": [
            {"address": t.address, "symbol": t.symbol, "decimals": t.decimals}
            for t in record.tokens
        ],
        "config": dict(record.config),
        "created_block": record.created_block,
        "discovered_by": dict(record.discovered_by),
        "status": record.status.value,
        "notes": record.notes,
    }
