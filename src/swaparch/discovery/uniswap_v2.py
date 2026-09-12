"""Canonical Uniswap V2 ``PairCreated`` discovery helpers.

The factory sorts token addresses, but both requested topic orderings are emitted
so a scan records its exact coverage rather than treating an empty reverse query
as an unstated assumption.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from eth_utils import keccak

from ..adapters.uniswap_v2 import FACTORY
from ..core.types import Address, PoolRecord, SupportStatus, Token, norm_address
from .uniswap_v3 import DISCOVERY_TOKENS, LogFilterSpec, unordered_pairs

FACTORY_CREATION_BLOCK = 10000835
PAIR_CREATED_SIG = "PairCreated(address,address,address,uint256)"
TOPIC_PAIR_CREATED = "0x" + keccak(text=PAIR_CREATED_SIG).hex()
assert TOPIC_PAIR_CREATED == "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"


def _topic_address(address: Address) -> str:
    return "0x" + norm_address(address)[2:].rjust(64, "0")


def build_pair_created_filters(
    tokens: Mapping[str, Address] | Iterable[Address] = DISCOVERY_TOKENS,
    from_block: int = FACTORY_CREATION_BLOCK,
    to_block: int | None = None,
    factory: Address = FACTORY,
) -> list[LogFilterSpec]:
    """Return one pair-filtered query per requested ordered token pair."""
    if to_block is None:
        raise ValueError("to_block must be pinned so coverage is checkable")
    if isinstance(tokens, Mapping):
        addresses = [norm_address(value) for value in tokens.values()]
        symbols = {norm_address(value): key for key, value in tokens.items()}
    else:
        addresses = [norm_address(value) for value in tokens]
        symbols = {value: value for value in addresses}
    specs: list[LogFilterSpec] = []
    for first, second in unordered_pairs(addresses):
        for token0, token1 in ((first, second), (second, first)):
            specs.append(
                LogFilterSpec(
                    address=norm_address(factory),
                    topics=(TOPIC_PAIR_CREATED, _topic_address(token0), _topic_address(token1)),
                    from_block=from_block,
                    to_block=to_block,
                    meta={
                        "event": "PairCreated",
                        "token0": token0,
                        "token1": token1,
                        "token0_symbol": symbols[token0],
                        "token1_symbol": symbols[token1],
                        "canonical": int(token0, 16) < int(token1, 16),
                    },
                )
            )
    return specs


def _block_number(log: Mapping[str, Any]) -> int | None:
    value = log.get("blockNumber")
    return None if value is None else int(value, 16) if isinstance(value, str) else int(value)


def _optional_hex(log: Mapping[str, Any], key: str) -> str | None:
    value = log.get(key)
    return value.lower() if isinstance(value, str) else value


def decode_pair_created(log: Mapping[str, Any]) -> dict[str, Any]:
    """Decode a raw ``PairCreated`` log without guessing token metadata."""
    topics = [str(topic).lower() for topic in log["topics"]]
    if len(topics) != 3 or topics[0] != TOPIC_PAIR_CREATED:
        raise ValueError("not a PairCreated log")
    data = str(log["data"])
    data = data.removeprefix("0x")
    if len(data) != 128:
        raise ValueError(f"PairCreated data must be 2 words, got {len(data) // 2} bytes")
    return {
        "token0": norm_address("0x" + topics[1][-40:]),
        "token1": norm_address("0x" + topics[2][-40:]),
        "pair": norm_address("0x" + data[:64][-40:]),
        "pair_index": int(data[64:], 16),
        "created_block": _block_number(log),
        "block_hash": _optional_hex(log, "blockHash"),
        "tx_hash": _optional_hex(log, "transactionHash"),
        "log_index": (
            int(log["logIndex"], 16) if isinstance(log.get("logIndex"), str) else log.get("logIndex")
        ),
    }


def pool_id(pair: Address, factory: Address = FACTORY) -> str:
    return f"uniswap_v2:{norm_address(factory)}:{norm_address(pair)}"


def pool_record_from_log(
    log: Mapping[str, Any],
    tokens: Mapping[Address, Token],
    chain: int = 1,
    factory: Address = FACTORY,
    filter_meta: Mapping[str, Any] | None = None,
    status: SupportStatus = SupportStatus.DISCOVERED_UNSUPPORTED,
) -> PoolRecord:
    """Build a discovered-but-unqualified V2 record from one creation log."""
    decoded = decode_pair_created(log)
    try:
        token0, token1 = tokens[decoded["token0"]], tokens[decoded["token1"]]
    except KeyError as exc:
        raise KeyError(f"no resolved Token for {exc.args[0]}") from None
    return PoolRecord(
        family="uniswap_v2",
        chain=chain,
        pool_id=pool_id(decoded["pair"], factory),
        deployment=norm_address(factory),
        pool=decoded["pair"],
        tokens=(token0, token1),
        config={"fee_numerator": 997, "fee_denominator": 1000, "transfer_semantics": {}},
        created_block=decoded["created_block"],
        discovered_by={
            "method": "logs:PairCreated",
            "deployment": norm_address(factory),
            "block_hash": decoded["block_hash"],
            "tx_hash": decoded["tx_hash"],
            "log_index": decoded["log_index"],
            "pair_index": decoded["pair_index"],
            **({"filter": dict(filter_meta)} if filter_meta else {}),
        },
        status=status,
        notes="Transfer semantics remain unqualified; raw stETH is excluded from this adapter.",
    )
