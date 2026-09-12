"""Ordered, integer-exact plan evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from swaparch.adapters.litepsm import LitePsmState
from swaparch.core.protocols import PoolState, Unsupported
from swaparch.core.types import Evaluation, Plan, StepResult


class Evaluator:
    def evaluate(self, plan: Plan, states: Iterable[PoolState]) -> Evaluation:
        if (type(plan.request.amount_in) is not int or plan.request.amount_in <= 0
                or plan.request.token_in.chain != plan.request.token_out.chain
                or plan.request.token_in.address == plan.request.token_out.address):
            return Evaluation(plan, 0, 0, plan.request.amount_in, (), False,
                              ("request requires positive input and distinct tokens on one chain",))
        pool_states = {state.record.pool_id: state for state in states}
        shared_reason = self._shared_capacity_reason(plan, pool_states)
        if shared_reason:
            return Evaluation(plan, 0, 0, plan.request.amount_in, (), False, (shared_reason,))

        balances: dict[str, int] = defaultdict(int)
        balances[plan.request.token_in.address] = plan.request.amount_in
        symbols = {
            token.address: token.symbol
            for state in pool_states.values()
            for token in state.tokens()
        }
        symbols[plan.request.token_in.address] = plan.request.token_in.symbol
        symbols[plan.request.token_out.address] = plan.request.token_out.symbol
        results: list[StepResult] = []
        reasons: list[str] = []
        psm_dai_buys: list[tuple[int, LitePsmState]] = []

        for index, step in enumerate(plan.steps):
            state = pool_states.get(step.pool_id)
            if state is None:
                reasons.append(f"step {index} references unknown pool {step.pool_id}")
                break
            if type(step.amount_in) is not int or step.amount_in < 0:
                reasons.append(f"step {index} requires nonnegative integer input")
                break
            if state.record.chain != plan.request.token_in.chain:
                reasons.append(f"step {index} pool belongs to a different chain")
                break
            held = balances[step.token_in]
            if step.amount_in > held:
                token = symbols.get(step.token_in, step.token_in)
                reasons.append(f"step {index} needs {step.amount_in} of unheld token {token}")
                break
            psm_dai_buy = (
                isinstance(state, LitePsmState)
                and step.amount_in > 0
                and step.token_in == state.dai.address
                and step.token_out == state.gem.address
            )
            try:
                amount_out, next_state = state.swap(
                    step.token_in, step.token_out, step.amount_in
                )
                gas = state.gas_estimate(step.token_in, step.token_out)
            except Unsupported as exc:
                reasons.append(f"step {index} unsupported: {exc}")
                break
            if type(amount_out) is not int or amount_out < 0:
                reasons.append(f"step {index} returned invalid output amount")
                break
            if psm_dai_buy:
                psm_dai_buys.append((index, state))
            balances[step.token_in] -= step.amount_in
            balances[step.token_out] += amount_out
            pool_states[step.pool_id] = next_state
            results.append(StepResult(step, amount_out, 0, gas))

        terminal_refund = self._terminal_psm_dai_refund(plan, balances, psm_dai_buys)
        if not reasons:
            endpoints = {plan.request.token_in.address, plan.request.token_out.address}
            for address, amount in balances.items():
                if address not in endpoints and amount and address not in terminal_refund:
                    reasons.append(f"stranded intermediate balance: {amount} {symbols.get(address, address)}")

        residual = balances[plan.request.token_in.address]
        if not reasons and residual:
            reasons.append("input token remains after execution")
        gas_values = [result.gas_estimate for result in results]
        gas_estimate = sum(gas_values) if gas_values and all(gas is not None for gas in gas_values) else None
        return Evaluation(
            plan=plan,
            amount_out=balances[plan.request.token_out.address],
            amount_in_spent=plan.request.amount_in - residual,
            residual_in=residual,
            steps=tuple(results),
            feasible=not reasons,
            reasons=tuple(reasons),
            gas_estimate=gas_estimate,
            terminal_refund=terminal_refund,
        )

    @staticmethod
    def _terminal_psm_dai_refund(
        plan: Plan, balances: dict[str, int], psm_dai_buys: list[tuple[int, LitePsmState]]
    ) -> dict[str, int]:
        """Return the one explicitly allowed zero-fee LitePSM lattice refund.

        A DAI input can miss ``buyGem``'s USDC-sized lattice by under one USDC
        raw unit.  This does not turn a partial PSM call into exact input: the
        returned DAI remains a separately reported trader balance.
        """
        if not plan.request.allow_psm_dai_refund or len(psm_dai_buys) != 1:
            return {}
        index, state = psm_dai_buys[0]
        refund = balances[state.dai.address]
        if state.tout != 0 or not 0 < refund < state.to18_conversion_factor:
            return {}
        # No later DAI production may be relabelled as the PSM lattice refund.
        if any(step.token_out == state.dai.address for step in plan.steps[index + 1:]):
            return {}
        return {state.dai.address: refund}

    @staticmethod
    def _shared_capacity_reason(plan: Plan, states: dict[str, PoolState]) -> str | None:
        owners: dict[str, str] = {}
        for pool_id in dict.fromkeys(step.pool_id for step in plan.steps):
            state = states.get(pool_id)
            if state is None:
                continue
            for capacity_id in state.capacity_ids():
                owner = owners.setdefault(capacity_id, pool_id)
                if owner != pool_id:
                    return "shared capacity not modelled"
        return None
