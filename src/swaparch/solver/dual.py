"""Numerical price-dual candidate guide for admitted V2/V3 states.

The value is a *numerical model estimate*, never an upper-bound claim.  The
model permits one aggregate directional trade per pool while the executable
candidate is recovered separately by ``GeneralSearchSolver`` and re-evaluated
with integer pool transitions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from math import exp
from time import perf_counter
from typing import Any

from swaparch.adapters.litepsm import LitePsmState
from swaparch.adapters.uniswap_v2 import (
    FEE_DENOMINATOR,
    FEE_NUMERATOR,
    UINT112_MAX,
    UniV2State,
)
from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.core.protocols import PoolState, Unsupported
from swaparch.core.types import Address, Plan, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.solver.baseline import BaselineSolver
from swaparch.solver.litepsm_support import finite_support
from swaparch.solver.search import GeneralSearchSolver
from swaparch.solver.v3_continuous import LoadedDomain, loaded_domain, price_optimal_support


@dataclass(frozen=True)
class _Action:
    pool_id: str
    token_in: Address
    token_out: Address
    amount_in: int
    amount_out: int
    value: float


@dataclass(frozen=True)
class _LocalSupport:
    """Local dual-model value and, when valid, an exact recovery proposal."""

    value: float
    action: _Action | None


class DualSolver:
    """Price-dual decomposition over V2, loaded-domain V3, and LitePSM models.

    SciPy's L-BFGS-B minimizes ``sum_i h_i(v) + v_in * q_in`` with the output
    token normalized to one. V2 proposes a continuous stationary input but the
    combined objective uses its rounded exact quote. V3 follows continuous
    intervals established by loaded bitmap words and liquidity-net updates.
    LitePSM uses a finite source-specific endpoint model and exact input lattice.
    """

    name = "numerical-dual"

    def __init__(
        self,
        *,
        max_steps: int = 8,
        beam_width: int = 128,
        max_expansions: int = 20_000,
        grid_parts: int = 32,
        dual_samples: int = 24,
    ) -> None:
        for name, value in (("max_steps", max_steps), ("beam_width", beam_width),
                            ("max_expansions", max_expansions), ("grid_parts", grid_parts),
                            ("dual_samples", dual_samples)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.max_steps = max_steps
        self.beam_width = beam_width
        self.max_expansions = max_expansions
        self.grid_parts = grid_parts
        self.dual_samples = dual_samples
        self.exclusions: list[dict[str, str]] = []
        self.last_diagnostics: dict[str, Any] = {}
        self._v3_domains: dict[tuple[str, Address, Address, int], dict[str, Any]] = {}
        self._v3_loaded_domains: dict[tuple[str, Address, Address], LoadedDomain] = {}
        self._v3_caps: dict[tuple[str, Address, Address, int], tuple[int, str]] = {}
        self._litepsm_supports: dict[tuple[str, Address, Address], dict[str, Any]] = {}
        self.terminal_refusals: list[str] = []
        self.recovery_diagnostics: dict[str, Any] = {}

    def solve(self, request: TradeRequest, states: Iterable[PoolState]) -> list[Plan]:
        BaselineSolver._validate_request(request)
        state_list = tuple(states)
        self.exclusions = []
        self._v3_domains = {}
        self._v3_loaded_domains = {}
        self._v3_caps = {}
        self._litepsm_supports = {}
        self.terminal_refusals = []
        self.recovery_diagnostics = {}
        admitted = tuple(state for state in state_list if self._admit(state))
        tokens = {token.address: token for state in admitted for token in state.tokens()}
        tokens[request.token_in.address] = request.token_in
        tokens[request.token_out.address] = request.token_out
        start = perf_counter()
        scipy = self._scipy()
        if scipy is None or not admitted:
            reason = "scipy_unavailable" if scipy is None else "no_admitted_modelled_states"
            self.last_diagnostics = self._diagnostics(
                status=reason, admitted=admitted, elapsed=perf_counter() - start
            )
            return self._fallback(request, state_list, reason)

        variable_tokens = tuple(sorted(address for address in tokens if address != request.token_out.address))
        decimals = {address: token.decimals for address, token in tokens.items()}
        cache: dict[tuple[float, ...], tuple[float, list[_LocalSupport]]] = {}

        def objective(log_prices: Any) -> float:
            values = {request.token_out.address: 1.0}
            values.update({address: exp(float(log_price)) for address, log_price in zip(variable_tokens, log_prices)})
            # Finite-difference optimizers probe very close log prices. Do not
            # round the cache key or distinct probes can receive a fabricated
            # zero derivative at small token prices.
            key = tuple(float(log_price) for log_price in log_prices)
            if key not in cache:
                supports = [self._support(state, values, decimals, request) for state in admitted]
                selected = [support for support in supports if support is not None]
                input_value = values[request.token_in.address] * self._units(request.amount_in, request.token_in.decimals)
                cache[key] = (input_value + sum(support.value for support in selected), selected)
            return cache[key][0]

        # Positive prices are represented in log-space.  Bounds prevent overflow
        # and are an optimizer setting, not economic price containment.
        result = scipy.minimize(
            objective,
            [0.0] * len(variable_tokens),
            method="L-BFGS-B",
            bounds=[(-20.0, 20.0)] * len(variable_tokens),
            options={"maxiter": 300, "ftol": 1e-12, "gtol": 1e-9},
        )
        values = {request.token_out.address: 1.0}
        values.update({address: exp(float(log_price)) for address, log_price in zip(variable_tokens, result.x)})
        supports = [support for state in admitted if (support := self._support(state, values, decimals, request))]
        suggestions: dict[tuple[str, Address, Address], list[int]] = {}
        for support in supports:
            action = support.action
            if action is None:
                continue
            if action.amount_in > 0:
                suggestions.setdefault((action.pool_id, action.token_in, action.token_out), []).append(
                    action.amount_in
                )
        self.last_diagnostics = self._diagnostics(
            status="converged" if result.success else "optimizer_failed",
            admitted=admitted,
            elapsed=perf_counter() - start,
            optimizer={
                "method": "L-BFGS-B",
                "success": bool(result.success),
                "message": str(result.message),
                "iterations": int(getattr(result, "nit", 0)),
                "model_objective": float(result.fun),
                "gradient_inf_norm": max(
                    (abs(float(value)) for value in getattr(result, "jac", ())), default=None
                ),
                "prices": values,
            },
            recovery_actions=len(suggestions),
        )
        recovery_search = GeneralSearchSolver(
            max_steps=self.max_steps,
            beam_width=self.beam_width,
            max_expansions=self.max_expansions,
            grid_parts=self.grid_parts,
            suggested_actions=suggestions,
            include_baseline=True,
        )
        plans = recovery_search.solve(request, state_list)
        self.terminal_refusals = list(recovery_search.terminal_refusals)
        self.recovery_diagnostics = dict(recovery_search.last_diagnostics)
        self.last_diagnostics["recovery_search"] = self.recovery_diagnostics
        evaluator = Evaluator()
        recovered: list[Plan] = []
        for plan in plans:
            exact = evaluator.evaluate(plan, state_list)
            recovered.append(
                Plan(
                    request=plan.request,
                    steps=plan.steps,
                    solver=self.name if plan.solver == "stateful-search" else plan.solver,
                    search_info={
                        **plan.search_info,
                        "dual": self.last_diagnostics,
                        "recovery": {
                            "method": "funded_stateful_search_then_fresh_evaluator",
                            "exact_amount_out": exact.amount_out,
                            "exact_feasible": exact.feasible,
                        },
                        "bound_status": "numerical_dual_estimate_not_a_bound",
                    },
                )
            )
        return recovered

    def _admit(self, state: PoolState) -> bool:
        if isinstance(state, (UniV2State, UniV3State, LitePsmState)):
            return True
        self.exclusions.append(
            {
                "pool": state.record.pool_id,
                "reason": "no_v2_v3_litepsm_dual_model; retained for stateful search",
            }
        )
        return False

    @staticmethod
    def _scipy() -> Any | None:
        try:
            from scipy import optimize
        except ImportError:
            return None
        return optimize

    @staticmethod
    def _units(amount: int, decimals: int) -> float:
        return float(amount) / (10 ** decimals)

    def _support(
        self,
        state: PoolState,
        values: dict[Address, float],
        decimals: dict[Address, int],
        request: TradeRequest,
    ) -> _LocalSupport | None:
        candidates: list[_LocalSupport] = []
        tokens = state.tokens()
        for token_in in tokens:
            for token_out in tokens:
                if token_in.address == token_out.address:
                    continue
                if isinstance(state, UniV2State):
                    action = self._v2_support(state, token_in.address, token_out.address, values, decimals)
                    candidate = None if action is None else _LocalSupport(action.value, action)
                elif isinstance(state, UniV3State):
                    candidate = self._v3_local_support(
                        state, token_in.address, token_out.address, values, decimals, request
                    )
                else:
                    candidate = self._litepsm_local_support(
                        state, token_in.address, token_out.address, values, decimals
                    )
                if candidate is not None and candidate.value > 0:
                    candidates.append(candidate)
        return max(candidates, default=None, key=lambda candidate: candidate.value)

    def _v2_support(
        self,
        state: UniV2State,
        token_in: Address,
        token_out: Address,
        values: dict[Address, float],
        decimals: dict[Address, int],
    ) -> _Action | None:
        reserve_in, reserve_out, _ = state._reserves_for(token_in, token_out)
        fee = FEE_NUMERATOR / FEE_DENOMINATOR
        pin = values[token_in] / (10 ** decimals[token_in])
        pout = values[token_out] / (10 ** decimals[token_out])
        # Maximize pout*f*x*Rout/(Rin+f*x) - pin*x.  The stationary point is
        # exact for this continuous V2 model.  A constant-product pool has no
        # artificial finite input cap: positive input value makes this maximum
        # finite whenever a profitable direction exists.
        if pin <= 0 or pout <= 0:
            return None
        stationary = (max(0.0, (pout * fee * reserve_out * reserve_in / pin) ** 0.5 - reserve_in) / fee)
        max_input = UINT112_MAX - reserve_in
        if max_input <= 0:
            return None
        amount_in = min(max_input, max(1, int(stationary)))
        try:
            amount_out = state.quote_exact_in(token_in, token_out, amount_in)
        except Unsupported:
            return None
        value = self._units(amount_out, decimals[token_out]) * values[token_out] - self._units(
            amount_in, decimals[token_in]
        ) * values[token_in]
        return _Action(state.record.pool_id, token_in, token_out, amount_in, amount_out, value)

    def _v3_support(
        self,
        state: UniV3State,
        token_in: Address,
        token_out: Address,
        values: dict[Address, float],
        decimals: dict[Address, int],
        request: TradeRequest,
    ) -> _Action | None:
        support = self._v3_local_support(state, token_in, token_out, values, decimals, request)
        return None if support is None else support.action

    def _v3_local_support(
        self,
        state: UniV3State,
        token_in: Address,
        token_out: Address,
        values: dict[Address, float],
        decimals: dict[Address, int],
        request: TradeRequest,
    ) -> _LocalSupport | None:
        seed = max(
            1,
            request.amount_in * 10 ** decimals[token_in] // 10 ** request.token_in.decimals,
        )
        key = (state.record.pool_id, token_in, token_out, seed)
        zero_for_one = state._direction(token_in, token_out)
        input_value = Decimal(str(values[token_in])) / Decimal(10 ** decimals[token_in])
        output_value = Decimal(str(values[token_out])) / Decimal(10 ** decimals[token_out])
        domain_key = (state.record.pool_id, token_in, token_out)
        domain = self._v3_loaded_domains.get(domain_key)
        if domain is None:
            domain = loaded_domain(state, zero_for_one)
            self._v3_loaded_domains[domain_key] = domain
        continuous, domain = price_optimal_support(
            state, zero_for_one, input_value, output_value, domain
        )
        limit = self._v3_caps.get(key)
        if limit is None:
            limit = self._v3_domain_limit(state, token_in, token_out, seed)
            self._v3_caps[key] = limit
        cap, cap_status = limit
        profile: dict[str, Any] = {
            "pool_id": state.record.pool_id,
            "token_in": token_in,
            "token_out": token_out,
            "seed": seed,
            "intervals": len(domain.intervals),
            "domain_stop": domain.stop,
            "support_model": "continuous loaded-tick intervals",
            "continuous_objective_status": "numerical_estimate_not_a_bound",
            "accepted_cap": cap,
            "accepted_cap_status": cap_status,
        }
        if continuous is None:
            self._v3_domains[key] = profile
            return None

        # Keep the existing exact-domain guard. It validates the integer seed
        # against the adapter and prevents a Decimal candidate from claiming an
        # unloaded continuation. The executable output is always requoted.
        integer_seed = max(1, int(continuous.gross_input))
        # A domain cap discovered from the request-scaled seed is independent
        # of price-unit conversion and preserves the previous exact guard.
        amount_in = min(integer_seed, cap)
        profile.update(
            continuous_objective=str(continuous.objective),
            continuous_gross_input=str(continuous.gross_input),
            continuous_output=str(continuous.output),
            continuous_end_sqrt_x96=continuous.end_sqrt_x96,
            continuous_boundary_tick=continuous.boundary_tick,
            continuous_stop=continuous.stop,
            integer_seed=integer_seed,
            accepted_cap=cap,
            quoted_amount_in=amount_in,
        )
        self._v3_domains[key] = profile
        if amount_in <= 0:
            return _LocalSupport(float(continuous.objective), None)
        try:
            amount_out = state.quote_exact_in(token_in, token_out, amount_in)
        except Unsupported:
            return _LocalSupport(float(continuous.objective), None)
        if amount_out <= 0:
            return _LocalSupport(float(continuous.objective), None)
        value = self._units(amount_out, decimals[token_out]) * values[token_out] - self._units(
            amount_in, decimals[token_in]
        ) * values[token_in]
        return _LocalSupport(
            float(continuous.objective),
            _Action(state.record.pool_id, token_in, token_out, amount_in, amount_out, value),
        )

    def _litepsm_local_support(
        self,
        state: LitePsmState,
        token_in: Address,
        token_out: Address,
        values: dict[Address, float],
        decimals: dict[Address, int],
    ) -> _LocalSupport | None:
        input_value = Decimal(str(values[token_in])) / Decimal(10 ** decimals[token_in])
        output_value = Decimal(str(values[token_out])) / Decimal(10 ** decimals[token_out])
        support = finite_support(state, token_in, token_out, input_value, output_value)
        key = (state.record.pool_id, token_in, token_out)
        self._litepsm_supports[key] = {
            "pool_id": state.record.pool_id,
            "token_in": token_in,
            "token_out": token_out,
            "direction": support.direction,
            "variable": support.variable,
            "capacity": support.capacity,
            "continuous_rate": str(support.continuous_rate),
            "continuous_input": str(support.continuous_input),
            "continuous_output": str(support.continuous_output),
            "continuous_objective": str(support.continuous_objective),
            "recommendation": support.recommendation,
            "tie_interval": None if support.tie_interval is None else tuple(map(str, support.tie_interval)),
            "capacity_status": support.capacity_status,
            "input_lattice": support.input_lattice,
            "model_status": support.model_status,
            "endpoint_input": None if support.endpoint_input is None else str(support.endpoint_input),
            "endpoint_output": None if support.endpoint_output is None else str(support.endpoint_output),
            "endpoint_objective": None if support.endpoint_objective is None else str(support.endpoint_objective),
            "endpoint_amount_in": support.endpoint_amount_in,
            "endpoint_amount_out": support.endpoint_amount_out,
        }
        if support.recommendation != "endpoint" or support.integer_amount_in is None:
            return None
        action = _Action(
            state.record.pool_id,
            token_in,
            token_out,
            support.integer_amount_in,
            support.integer_amount_out or 0,
            self._units(support.integer_amount_out or 0, decimals[token_out]) * values[token_out]
            - self._units(support.integer_amount_in, decimals[token_in]) * values[token_in],
        )
        return _LocalSupport(float(support.continuous_objective), action)

    @staticmethod
    def _v3_domain_cap(state: UniV3State, token_in: Address, token_out: Address, seed: int) -> int:
        """Compatibility helper returning the accepted exact-quote amount.

        Inspect :meth:`_v3_domain_limit` status before treating this number as
        an exact domain edge.
        """
        return DualSolver._v3_domain_limit(state, token_in, token_out, seed)[0]

    @staticmethod
    def _v3_domain_limit(
        state: UniV3State, token_in: Address, token_out: Address, seed: int
    ) -> tuple[int, str]:
        """Find the accepted finite local domain by exact quotes, never by assuming
        unloaded words are empty liquidity.

        The fixed doubling budget is a work bound. When it is exhausted the
        returned amount is an exact-quote *lower bound*, not a claimed edge.
        """
        probe = max(1, seed)
        try:
            state.quote_exact_in(token_in, token_out, probe)
        except Unsupported:
            try:
                state.quote_exact_in(token_in, token_out, 1)
            except Unsupported:
                return 0, "no_accepted_quote"
            low, high = 1, probe
        else:
            low, high = probe, probe * 2
            for _ in range(32):
                try:
                    state.quote_exact_in(token_in, token_out, high)
                except Unsupported:
                    break
                low, high = high, high * 2
            else:
                return low, "verified_lower_bound_doubling_budget"
        while low + 1 < high:
            middle = (low + high) // 2
            try:
                state.quote_exact_in(token_in, token_out, middle)
            except Unsupported:
                high = middle
            else:
                low = middle
        return low, "exact_accepted_prefix_cap"

    def _fallback(self, request: TradeRequest, states: tuple[PoolState, ...], reason: str) -> list[Plan]:
        recovery_search = GeneralSearchSolver(
            max_steps=self.max_steps,
            beam_width=self.beam_width,
            max_expansions=self.max_expansions,
            grid_parts=self.grid_parts,
            include_baseline=True,
        )
        plans = recovery_search.solve(request, states)
        self.terminal_refusals = list(recovery_search.terminal_refusals)
        self.recovery_diagnostics = dict(recovery_search.last_diagnostics)
        self.last_diagnostics["recovery_search"] = self.recovery_diagnostics
        return [
            Plan(
                request=plan.request,
                steps=plan.steps,
                solver=self.name if plan.solver == "stateful-search" else plan.solver,
                search_info={
                    **plan.search_info,
                    "dual": self.last_diagnostics,
                    "recovery": "dual_unavailable_funded_stateful_search",
                    "bound_status": "no_dual_estimate",
                    "fallback_reason": reason,
                },
            )
            for plan in plans
        ]

    def _diagnostics(
        self, *, status: str, admitted: tuple[PoolState, ...], elapsed: float, **extra: Any
    ) -> dict[str, Any]:
        return {
            "status": status,
            "runtime_seconds": elapsed,
            "model_coverage": {
                "admitted": [state.record.pool_id for state in admitted],
                "excluded": list(self.exclusions),
                "v2": sum(isinstance(state, UniV2State) for state in admitted),
                "v2_support_model": "stationary input then rounded integer exact quote",
                # Compatibility field retained for existing manifest readers;
                # inspect v3_support_model for the current semantics.
                "v3_loaded_finite_domain": sum(isinstance(state, UniV3State) for state in admitted),
                "v3_loaded_continuous_domain": sum(isinstance(state, UniV3State) for state in admitted),
                "v3_support_model": "continuous loaded-tick intervals; no global containment or bound",
                "v3_domain_profiles": list(self._v3_domains.values()),
                "litepsm": sum(isinstance(state, LitePsmState) for state in admitted),
                "litepsm_support_model": (
                    "finite source-specific endpoint with exact-input lattice; "
                    "numerical estimate, no containment or bound"
                ),
                "litepsm_support_profiles": list(self._litepsm_supports.values()),
            },
            **extra,
        }
