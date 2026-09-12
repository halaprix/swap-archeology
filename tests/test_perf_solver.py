import importlib.util
from pathlib import Path

from swaparch.core.types import Plan, PoolRecord, Step, SupportStatus, Token, TradeRequest
from swaparch.evaluator.evaluate import Evaluator
from swaparch.evaluator.fakes import ConstantProductPoolState
from swaparch.solver.baseline import BaselineSolver, _Route
from swaparch.solver.search import GeneralSearchSolver

_SPEC = importlib.util.spec_from_file_location(
    "perf_solver", Path(__file__).parents[1] / "scripts" / "perf_solver.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
evaluator_plan_memo = _MODULE.evaluator_plan_memo
baseline_ordered_prefix_memo = _MODULE.baseline_ordered_prefix_memo
baseline_indexed_split_quotes = _MODULE.baseline_indexed_split_quotes
search_nonterminal_residual_skip = _MODULE.search_nonterminal_residual_skip
search_static_metadata = _MODULE.search_static_metadata


def test_evaluator_plan_memo_rebinds_plan_and_restores_method() -> None:
    token_a = Token(1, "0x0000000000000000000000000000000000000001", "A", 18)
    token_b = Token(1, "0x0000000000000000000000000000000000000002", "B", 18)
    state = ConstantProductPoolState(
        PoolRecord(
            family="fake", chain=1, pool_id="pool",
            deployment="0x0000000000000000000000000000000000000010",
            pool="0x0000000000000000000000000000000000000011",
            tokens=(token_a, token_b), config={}, created_block=0,
            discovered_by={"method": "test"}, status=SupportStatus.SUPPORTED,
        ),
        100,
        100,
    )
    request = TradeRequest(token_a, token_b, 10)
    steps = (Step("pool", token_a.address, token_b.address, 10),)
    first = Plan(request, steps, "search", {"expansions": 1})
    second = Plan(request, steps, "search", {"expansions": 2})
    original = Evaluator.evaluate
    try:
        with evaluator_plan_memo((state,), maxsize=2) as memo:
            initial = Evaluator().evaluate(first, (state,))
            repeated = Evaluator().evaluate(second, (state,))
            assert repeated.plan is second
            assert repeated.amount_out == initial.amount_out
            assert memo.stats() == {"hits": 1, "misses": 1, "size": 1, "maxsize": 2}
            raise RuntimeError("exercise finally")
    except RuntimeError:
        pass
    assert Evaluator.evaluate is original


def test_baseline_prefix_memo_replays_ordered_overlap_and_restores_method() -> None:
    token_a = Token(1, "0x0000000000000000000000000000000000000003", "A", 18)
    token_b = Token(1, "0x0000000000000000000000000000000000000004", "B", 18)
    token_c = Token(1, "0x0000000000000000000000000000000000000005", "C", 18)
    def state(pool_id, tokens):
        return ConstantProductPoolState(
            PoolRecord("fake", 1, pool_id, "0x0000000000000000000000000000000000000010", "0x0000000000000000000000000000000000000011", tokens, {}, 0, {"method": "test"}, SupportStatus.SUPPORTED),
            1_000,
            1_000,
        )
    ab, bc = state("ab", (token_a, token_b)), state("bc", (token_b, token_c))
    first = _Route((("ab", token_a.address, token_b.address), ("bc", token_b.address, token_c.address)))
    second = _Route((("ab", token_a.address, token_b.address),))
    solver = BaselineSolver()
    original = BaselineSolver._ordered_output
    original_best_split = BaselineSolver._best_split
    request = TradeRequest(token_a, token_b, 20)
    try:
        with baseline_ordered_prefix_memo((ab, bc), maxsize=2) as memo:
            first_result = solver._best_split([first, second], request, (ab, bc), {"ab": ab, "bc": bc})
            repeated = solver._best_split([first, second], request, (ab, bc), {"ab": ab, "bc": bc})
            assert repeated == first_result
            assert memo.stats()["hits"] > 0
            raise RuntimeError("exercise finally")
    except RuntimeError:
        pass
    assert BaselineSolver._ordered_output is original
    assert BaselineSolver._best_split is original_best_split


def test_search_residual_skip_keeps_results_and_terminal_refusals() -> None:
    token_a = Token(1, "0x0000000000000000000000000000000000000006", "A", 18)
    token_b = Token(1, "0x0000000000000000000000000000000000000007", "B", 18)
    state = ConstantProductPoolState(
        PoolRecord("fake", 1, "pool", "0x0000000000000000000000000000000000000010", "0x0000000000000000000000000000000000000011", (token_a, token_b), {}, 0, {"method": "test"}, SupportStatus.SUPPORTED),
        1_000,
        1_000,
    )
    request = TradeRequest(token_a, token_b, 20)
    kwargs = {"max_steps": 2, "beam_width": 32, "grid_parts": 4, "include_baseline": False}
    reference = GeneralSearchSolver(**kwargs)
    expected = reference.solve(request, (state,))
    original = GeneralSearchSolver.solve
    try:
        with search_nonterminal_residual_skip():
            actual_solver = GeneralSearchSolver(**kwargs)
            actual = actual_solver.solve(request, (state,))
            assert actual == expected
            assert actual_solver.last_diagnostics == reference.last_diagnostics
            assert actual_solver.unsupported == reference.unsupported
        raise RuntimeError("exercise finally")
    except RuntimeError:
        pass
    assert GeneralSearchSolver.solve is original


def test_indexed_split_quotes_keep_baseline_plans_and_diagnostics() -> None:
    token_a = Token(1, "0x0000000000000000000000000000000000000008", "A", 18)
    token_b = Token(1, "0x0000000000000000000000000000000000000009", "B", 18)
    def state(pool_id, reserve0, reserve1):
        return ConstantProductPoolState(
            PoolRecord("fake", 1, pool_id, "0x0000000000000000000000000000000000000010", "0x0000000000000000000000000000000000000011", (token_a, token_b), {}, 0, {"method": "test"}, SupportStatus.SUPPORTED),
            reserve0,
            reserve1,
        )
    states = (state("one", 90, 120), state("two", 140, 140), state("three", 500, 440))
    request = TradeRequest(token_a, token_b, 37)
    reference = BaselineSolver(intermediates=(), grid_parts=7)
    expected = reference.solve(request, states)
    original = BaselineSolver._best_split
    try:
        with baseline_indexed_split_quotes():
            actual_solver = BaselineSolver(intermediates=(), grid_parts=7)
            assert actual_solver.solve(request, states) == expected
            assert actual_solver.unsupported == reference.unsupported
        raise RuntimeError("exercise finally")
    except RuntimeError:
        pass
    assert BaselineSolver._best_split is original


def test_search_static_metadata_keeps_search_result_and_restores_methods() -> None:
    token_a = Token(1, "0x0000000000000000000000000000000000000012", "A", 18)
    token_b = Token(1, "0x0000000000000000000000000000000000000013", "B", 18)
    state = ConstantProductPoolState(
        PoolRecord("fake", 1, "pool", "0x0000000000000000000000000000000000000010", "0x0000000000000000000000000000000000000011", (token_a, token_b), {}, 0, {"method": "test"}, SupportStatus.SUPPORTED),
        1_000,
        1_000,
    )
    request = TradeRequest(token_a, token_b, 20)
    kwargs = {"max_steps": 2, "beam_width": 32, "grid_parts": 4, "include_baseline": False}
    reference = GeneralSearchSolver(**kwargs)
    expected = reference.solve(request, (state,))
    original = GeneralSearchSolver.solve
    with search_static_metadata():
        actual_solver = GeneralSearchSolver(**kwargs)
        assert actual_solver.solve(request, (state,)) == expected
        assert actual_solver.last_diagnostics == reference.last_diagnostics
    assert GeneralSearchSolver.solve is original
