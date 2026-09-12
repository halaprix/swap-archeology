"""Uniswap V3 source adapter: exact integer math, per-block state, read plan."""

from .adapter import (
    DEFAULT_WORD_RADIUS,
    SEL_FEE,
    SEL_LIQUIDITY,
    SEL_SLOT0,
    SEL_TICK_BITMAP,
    SEL_TICK_SPACING,
    SEL_TICKS,
    SEL_TOKEN0,
    SEL_TOKEN1,
    UniswapV3Adapter,
)
from .state import GAS_ESTIMATE, UniV3State

__all__ = [
    "DEFAULT_WORD_RADIUS",
    "GAS_ESTIMATE",
    "SEL_FEE",
    "SEL_LIQUIDITY",
    "SEL_SLOT0",
    "SEL_TICKS",
    "SEL_TICK_BITMAP",
    "SEL_TICK_SPACING",
    "SEL_TOKEN0",
    "SEL_TOKEN1",
    "UniV3State",
    "UniswapV3Adapter",
]
