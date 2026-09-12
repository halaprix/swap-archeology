"""Small integer-exact pool used by evaluator tests."""

from __future__ import annotations

from dataclasses import dataclass, replace

from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address, PoolRecord, Token


@dataclass(frozen=True)
class ConstantProductPoolState:
    record: PoolRecord
    reserve0: int
    reserve1: int
    shared_capacity_ids: tuple[str, ...] | None = None

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return self.shared_capacity_ids or (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        reserve_in, reserve_out = self._reserves(token_in, token_out)
        if amount_in < 0:
            raise Unsupported("negative input")
        amount_with_fee = amount_in * 9_970
        return amount_with_fee * reserve_out // (reserve_in * 10_000 + amount_with_fee)

    def swap(
        self, token_in: Address, token_out: Address, amount_in: int
    ) -> tuple[int, ConstantProductPoolState]:
        amount_out = self.quote_exact_in(token_in, token_out, amount_in)
        token0, token1 = self.record.tokens
        if token_in == token0.address and token_out == token1.address:
            return amount_out, replace(
                self,
                reserve0=self.reserve0 + amount_in,
                reserve1=self.reserve1 - amount_out,
            )
        return amount_out, replace(
            self,
            reserve0=self.reserve0 - amount_out,
            reserve1=self.reserve1 + amount_in,
        )

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self._reserves(token_in, token_out)
        return 100_000

    def _reserves(self, token_in: Address, token_out: Address) -> tuple[int, int]:
        token0, token1 = self.record.tokens
        if token_in == token0.address and token_out == token1.address:
            return self.reserve0, self.reserve1
        if token_in == token1.address and token_out == token0.address:
            return self.reserve1, self.reserve0
        raise Unsupported("token direction not in pool")

