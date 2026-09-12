from dataclasses import dataclass

from swaparch.adapters.litepsm import DAI, USDC, LitePsmState
from swaparch.core.protocols import Unsupported
from swaparch.core.types import Address, Plan, PoolRecord, Step, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.evaluator.fakes import ConstantProductPoolState
from swaparch.solver.search import GeneralSearchSolver

A = Token(1, "0x0000000000000000000000000000000000000001", "A", 18)
B = Token(1, "0x0000000000000000000000000000000000000002", "B", 18)
C = Token(1, "0x0000000000000000000000000000000000000003", "C", 18)
DAI_TOKEN = Token(1, DAI, "DAI", 18)
USDC_TOKEN = Token(1, USDC, "USDC", 6)


@dataclass(frozen=True)
class ExactRate:
    record: PoolRecord
    output: int

    def tokens(self) -> tuple[Token, ...]:
        return self.record.tokens

    def capacity_ids(self) -> tuple[str, ...]:
        return (self.record.pool_id,)

    def quote_exact_in(self, token_in: Address, token_out: Address, amount_in: int) -> int:
        if (token_in, token_out, amount_in) != (A.address, DAI_TOKEN.address, 1):
            raise Unsupported("fixture only prices one exact input")
        return self.output

    def swap(self, token_in: Address, token_out: Address, amount_in: int):
        return self.quote_exact_in(token_in, token_out, amount_in), self

    def gas_estimate(self, token_in: Address, token_out: Address) -> int | None:
        self.quote_exact_in(token_in, token_out, 1)
        return None


def pool(
    pool_id: str,
    token0: Token,
    token1: Token,
    reserve0: int = 10_000,
    reserve1: int = 10_000,
    capacity_ids: tuple[str, ...] | None = None,
) -> ConstantProductPoolState:
    record = PoolRecord(
        family="fake",
        chain=1,
        pool_id=pool_id,
        deployment="0x0000000000000000000000000000000000000010",
        pool="0x0000000000000000000000000000000000000011",
        tokens=(token0, token1),
        config={},
        created_block=0,
        discovered_by={"method": "test"},
        status=SupportStatus.SUPPORTED,
    )
    return ConstantProductPoolState(record, reserve0, reserve1, capacity_ids)


def plan(amount: int, steps: tuple[Step, ...], token_out: Token = C) -> Plan:
    return Plan(TradeRequest(A, token_out, amount), steps, "test")


def test_steps_are_applied_in_order() -> None:
    states = (pool("ab", A, B), pool("bc", B, C, reserve1=20_000))
    forward = plan(
        100,
        (
            Step("ab", A.address, B.address, 100),
            Step("bc", B.address, C.address, 98),
        ),
    )
    reverse = plan(
        100,
        (
            Step("bc", B.address, C.address, 98),
            Step("ab", A.address, B.address, 100),
        ),
    )

    assert Evaluator().evaluate(forward, states).feasible
    failed = Evaluator().evaluate(reverse, states)
    assert not failed.feasible
    assert failed.reasons == ("step 0 needs 98 of unheld token B",)


def test_reusing_a_pool_consumes_its_updated_state() -> None:
    state = pool("ab", A, B)
    evaluation = Evaluator().evaluate(
        plan(
            200,
            (
                Step("ab", A.address, B.address, 100),
                Step("ab", A.address, B.address, 100),
            ),
            B,
        ),
        (state,),
    )

    assert evaluation.feasible
    assert evaluation.amount_out < 2 * state.quote_exact_in(A.address, B.address, 100)


def test_unspent_input_is_reported_as_residual() -> None:
    evaluation = Evaluator().evaluate(
        plan(100, (Step("ab", A.address, B.address, 50),), B),
        (pool("ab", A, B),),
    )

    assert evaluation.amount_in_spent == 50
    assert evaluation.residual_in == 50
    assert not evaluation.feasible
    assert "input token remains after execution" in evaluation.reasons


def test_plan_cannot_spend_inventory_the_trader_does_not_hold() -> None:
    evaluation = Evaluator().evaluate(
        plan(100, (Step("bc", B.address, C.address, 1),)),
        (pool("bc", B, C),),
    )

    assert not evaluation.feasible
    assert evaluation.reasons == ("step 0 needs 1 of unheld token B",)


def test_first_hop_allocations_sum_to_requested_input() -> None:
    evaluation = Evaluator().evaluate(
        plan(
            100,
            (
                Step("ab-1", A.address, B.address, 40),
                Step("ab-2", A.address, B.address, 60),
            ),
            B,
        ),
        (pool("ab-1", A, B), pool("ab-2", A, B)),
    )

    assert evaluation.feasible
    assert evaluation.amount_in_spent == 100
    assert evaluation.residual_in == 0


def test_overlapping_capacity_between_distinct_pools_is_refused() -> None:
    evaluation = Evaluator().evaluate(
        plan(
            100,
            (
                Step("ab-1", A.address, B.address, 50),
                Step("ab-2", A.address, B.address, 50),
            ),
            B,
        ),
        (
            pool("ab-1", A, B, capacity_ids=("shared-vault",)),
            pool("ab-2", A, B, capacity_ids=("shared-vault",)),
        ),
    )

    assert not evaluation.feasible
    assert evaluation.reasons == ("shared capacity not modelled",)


def test_stranded_intermediate_balance_is_not_a_full_route() -> None:
    evaluation = Evaluator().evaluate(
        plan(100, (Step("ab", A.address, B.address, 100),
                   Step("bc", B.address, C.address, 97))),
        (pool("ab", A, B), pool("bc", B, C)),
    )
    assert not evaluation.feasible
    assert evaluation.reasons == ("stranded intermediate balance: 1 B",)


def test_invalid_request_is_not_feasible() -> None:
    assert not Evaluator().evaluate(plan(-1, ()), ()).feasible
    assert not Evaluator().evaluate(plan(1, (), A), ()).feasible
    assert not Evaluator().evaluate(plan(100.5, ()), ()).feasible
    assert not Evaluator().evaluate(plan(True, ()), ()).feasible


def test_round_trip_residual_is_not_full_input_consumption() -> None:
    evaluation = Evaluator().evaluate(
        plan(100, (Step("ab", A.address, B.address, 100),
                   Step("ab", B.address, A.address, 98))),
        (pool("ab", A, B),),
    )
    assert not evaluation.feasible
    assert evaluation.residual_in == 98
    assert evaluation.reasons == ("input token remains after execution",)


def test_funded_cycle_can_respend_returned_input_and_finish_without_residual() -> None:
    # Independent integer transitions:100 A buys98 B; reversing that shared pool
    # returns98 A, which buys96 C. Gross A turnover198 does not create funding.
    states = (pool("ab", A, B), pool("ac", A, C))
    evaluation = Evaluator().evaluate(
        plan(100, (Step("ab", A.address, B.address, 100),
                   Step("ab", B.address, A.address, 98),
                   Step("ac", A.address, C.address, 98))), states)
    assert evaluation.feasible
    assert evaluation.amount_in_spent == 100
    assert evaluation.residual_in == 0
    assert evaluation.amount_out == 96
    assert [row.amount_out for row in evaluation.steps] == [98, 98, 96]


def test_opt_in_zero_fee_litepsm_dai_lattice_refund_is_explicit_and_scoped() -> None:
    upstream_record = pool("a-dai", A, DAI_TOKEN).record
    upstream = ExactRate(upstream_record, 2 * 10**12 + 7)
    psm_record = PoolRecord(
        family="maker_sky_psm", chain=1, pool_id="psm", deployment="0xf6e72db5454dd049d0788e411b06cfaf16853042",
        pool="0xf6e72db5454dd049d0788e411b06cfaf16853042", tokens=(DAI_TOKEN, USDC_TOKEN),
        config={"model": "dss-lite-psm"}, created_block=0, discovered_by={"method": "test"},
        status=SupportStatus.SUPPORTED,
    )
    psm = LitePsmState(psm_record, DAI_TOKEN, USDC_TOKEN, 10**12, 0, 0, 0, 10, 10)
    steps = (
        Step("a-dai", A.address, DAI_TOKEN.address, 1),
        Step("psm", DAI_TOKEN.address, USDC_TOKEN.address, 2 * 10**12),
    )
    strict = Evaluator().evaluate(Plan(TradeRequest(A, USDC_TOKEN, 1), steps, "test"), (upstream, psm))
    assert not strict.feasible
    assert strict.reasons == ("stranded intermediate balance: 7 DAI",)

    request = TradeRequest(A, USDC_TOKEN, 1, allow_psm_dai_refund=True)
    evaluation = Evaluator().evaluate(Plan(request, steps, "test"), (upstream, psm))
    assert evaluation.feasible
    assert evaluation.amount_in_spent == 1
    assert evaluation.amount_out == 2
    assert evaluation.terminal_refund == {DAI_TOKEN.address: 7}

    zero_input = Plan(
        request,
        (Step("a-dai", A.address, DAI_TOKEN.address, 1),
         Step("psm", DAI_TOKEN.address, USDC_TOKEN.address, 0)),
        "test",
    )
    zero_input_state = ExactRate(upstream_record, 7)
    zero_input_evaluation = Evaluator().evaluate(zero_input, (zero_input_state, psm))
    assert not zero_input_evaluation.feasible
    assert zero_input_evaluation.reasons == ("stranded intermediate balance: 7 DAI",)
    assert zero_input_evaluation.terminal_refund == {}

    plans = GeneralSearchSolver(max_steps=2, grid_parts=1, include_baseline=False).solve(request, (upstream, psm))
    assert len(plans) == 1
    assert Evaluator().evaluate(plans[0], (upstream, psm)).terminal_refund == {DAI_TOKEN.address: 7}
