"""Finite endpoint support for the source-qualified LitePSM state.

This module is deliberately source-specific. It exposes a numerical local
candidate and an independently replayed exact seed; it does not admit LitePSM
to a dual solver or claim a global bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from swaparch.adapters.litepsm import HALTED, MAX_UINT256, WAD, LitePsmState
from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address


@dataclass(frozen=True)
class LitePsmSupport:
    """One directional finite-rate local support result.

    ``capacity`` is denominated in the indicated ``variable``. BuyGem's
    variable is GEM output because its source entrypoint accepts output and
    accepts only a lattice of matching DAI exact inputs.
    """

    pool_id: str
    direction: str
    variable: str
    capacity: int
    continuous_rate: Decimal
    continuous_input: Decimal
    continuous_output: Decimal
    continuous_objective: Decimal
    recommendation: str
    tie_interval: tuple[Decimal, Decimal] | None
    integer_amount_in: int | None
    integer_amount_out: int | None
    capacity_status: str
    input_lattice: str | None
    model_status: str = "numerical_estimate_not_a_bound"
    # Endpoint fields are diagnostics.  They stay populated when the selected
    # support is zero or a tie, while ``integer_amount_*`` remains reserved for
    # a positive endpoint recovery action.
    endpoint_input: Decimal | None = None
    endpoint_output: Decimal | None = None
    endpoint_objective: Decimal | None = None
    endpoint_amount_in: int | None = None
    endpoint_amount_out: int | None = None


def max_sell_gem_input(state: LitePsmState) -> int:
    """Largest positive-output GEM exact input accepted by the current state."""

    if state.tin == HALTED or state.tin >= WAD:
        return 0
    max_input = MAX_UINT256 // state.to18_conversion_factor
    low, high = 0, max_input + 1  # high fails checked gross multiplication.
    while low + 1 < high:
        middle = (low + high) // 2
        try:
            state.quote_exact_in(state.gem.address, state.dai.address, middle)
        except Unsupported:
            high = middle
        else:
            low = middle
    if low == 0:
        return 0
    try:
        return low if state.quote_exact_in(state.gem.address, state.dai.address, low) > 0 else 0
    except Unsupported:  # pragma: no cover - binary predicate establishes this
        return 0


def max_buy_gem_output(state: LitePsmState) -> int:
    """Largest GEM output whose source-defined exact input is executable.

    Inventory and allowance alone are insufficient: buyGem's gross/fee
    arithmetic and adding its DAI input to the PSM's DAI balance are checked
    uint256 operations too.  Quote each binary-search probe so this prefix is
    defined by the immutable state implementation, rather than duplicating a
    partial subset of its conditions.
    """

    if state.tout == HALTED:
        return 0
    inventory_limit = min(state.pocket_gem, state.pocket_gem_allowance)
    low, high = 0, inventory_limit + 1
    while low + 1 < high:
        middle = (low + high) // 2
        try:
            required = state._buy_gem_required_dai(middle)
            output = state.quote_exact_in(state.dai.address, state.gem.address, required)
        except Unsupported:
            high = middle
        else:
            if output != middle:  # defensive: exact-input preimage must agree.
                high = middle
            else:
                low = middle
    return low


def finite_support(
    state: LitePsmState,
    token_in: Address,
    token_out: Address,
    input_value_per_raw: Decimal,
    output_value_per_raw: Decimal,
) -> LitePsmSupport:
    """Return the zero/endpoint/tie support for one LitePSM direction.

    Arithmetic is scoped to Decimal precision and the returned integer proposal
    is replayed through ``LitePsmState.quote_exact_in`` before it is exposed.
    """

    direction = state._direction(token_in, token_out)
    with localcontext() as context:
        context.prec = 96
        if input_value_per_raw <= 0 or output_value_per_raw <= 0:
            raise ValueError("LitePSM support prices must be positive")
        if direction == "gem_to_dai":
            return _sell_support(state, input_value_per_raw, output_value_per_raw)
        return _buy_support(state, input_value_per_raw, output_value_per_raw)


def _sell_support(
    state: LitePsmState, input_value_per_raw: Decimal, output_value_per_raw: Decimal
) -> LitePsmSupport:
    if state.tin == HALTED:
        return _inactive(state, "gem_to_dai", "gem_input", "halted")
    if state.tin == WAD:
        return _inactive(state, "gem_to_dai", "gem_input", "fee_100_percent")
    capacity = max_sell_gem_input(state)
    rate = Decimal(state.to18_conversion_factor) * (Decimal(WAD - state.tin) / Decimal(WAD))
    return _endpoint(
        state, "gem_to_dai", "gem_input", capacity, rate,
        input_value_per_raw, output_value_per_raw, None,
    )


def _buy_support(
    state: LitePsmState, input_value_per_raw: Decimal, output_value_per_raw: Decimal
) -> LitePsmSupport:
    if state.tout == HALTED:
        return _inactive(state, "dai_to_gem", "gem_output", "halted")
    capacity = max_buy_gem_output(state)
    # Optimize in GEM output. The exact DAI input is a source-defined lattice.
    cost_per_gem = Decimal(state.to18_conversion_factor) * (
        Decimal(WAD + state.tout) / Decimal(WAD)
    )
    return _endpoint(
        state, "dai_to_gem", "gem_output", capacity, cost_per_gem,
        input_value_per_raw, output_value_per_raw,
        "required_dai(g)=factor*g+floor(factor*g*tout/WAD)",
    )


def _endpoint(
    state: LitePsmState,
    direction: str,
    variable: str,
    capacity: int,
    rate: Decimal,
    input_value_per_raw: Decimal,
    output_value_per_raw: Decimal,
    input_lattice: str | None,
) -> LitePsmSupport:
    if capacity <= 0:
        return LitePsmSupport(
            state.record.pool_id, direction, variable, 0, rate, Decimal(0), Decimal(0), Decimal(0),
            "no_trade", None, None, None, "no_exact_valid_prefix", input_lattice,
        )
    if direction == "gem_to_dai":
        continuous_input = Decimal(capacity)
        continuous_output = rate * continuous_input
        amount_in = capacity
    else:
        continuous_output = Decimal(capacity)
        continuous_input = rate * continuous_output
        amount_in = state._buy_gem_required_dai(capacity)
    objective = continuous_output * output_value_per_raw - continuous_input * input_value_per_raw
    try:
        amount_out = state.quote_exact_in(
            state.gem.address if direction == "gem_to_dai" else state.dai.address,
            state.dai.address if direction == "gem_to_dai" else state.gem.address,
            amount_in,
        )
    except Unsupported:  # pragma: no cover - exact prefix search established this
        return LitePsmSupport(
            state.record.pool_id, direction, variable, capacity, rate,
            Decimal(0), Decimal(0), Decimal(0), "no_trade", None,
            None, None, "prefix_replay_inconsistent", input_lattice,
            endpoint_input=continuous_input, endpoint_output=continuous_output,
            endpoint_objective=objective,
        )

    if objective < 0:
        recommendation, tie = "no_trade", None
    elif objective == 0:
        recommendation, tie = "tie", (Decimal(0), Decimal(capacity))
    else:
        recommendation, tie = "endpoint", None
    if recommendation != "endpoint":
        return LitePsmSupport(
            state.record.pool_id, direction, variable, capacity, rate, Decimal(0), Decimal(0), Decimal(0),
            recommendation, tie, None, None,
            "exact_replayed_tie_not_selected" if recommendation == "tie"
            else "exact_replayed_unprofitable_not_selected",
            input_lattice,
            endpoint_input=continuous_input, endpoint_output=continuous_output,
            endpoint_objective=objective, endpoint_amount_in=amount_in, endpoint_amount_out=amount_out,
        )
    return LitePsmSupport(
        state.record.pool_id, direction, variable, capacity, rate,
        continuous_input, continuous_output, objective, "endpoint", None,
        amount_in, amount_out, "exact_replayed_endpoint", input_lattice,
        endpoint_input=continuous_input, endpoint_output=continuous_output,
        endpoint_objective=objective, endpoint_amount_in=amount_in, endpoint_amount_out=amount_out,
    )


def _inactive(state: LitePsmState, direction: str, variable: str, status: str) -> LitePsmSupport:
    return LitePsmSupport(
        state.record.pool_id, direction, variable, 0, Decimal(0), Decimal(0), Decimal(0), Decimal(0),
        "no_trade", None, None, None, status, None,
    )
