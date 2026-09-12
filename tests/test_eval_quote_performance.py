import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "eval_quote_performance", Path(__file__).parents[1] / "scripts" / "eval_quote_performance.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
initial_swap_memo = _MODULE.initial_swap_memo


class State:
    def __init__(self, value: int):
        self.value = value

    def swap(self, token_in, token_out, amount):
        if amount < 0:
            raise ValueError("bad amount")
        return amount + self.value, State(self.value + 1)


def test_initial_swap_memo_is_identity_scoped_and_restored_on_exception():
    state = State(1)
    original = State.swap
    try:
        with initial_swap_memo((state,), maxsize=2, classes=(State,)) as memo:
            first = state.swap("a", "b", 4)
            assert state.swap("a", "b", 4) is first
            assert memo.stats()["hits"] == 1
            assert state.swap("a", "b", 5) != first
            assert first[1].swap("a", "b", 4)[0] == 6  # returned state bypasses cache
            try:
                state.swap("a", "b", -1)
            except ValueError:
                pass
            else:
                assert False
            assert memo.stats()["size"] == 2  # failures never enter the cache
            raise RuntimeError("exercise finally")
    except RuntimeError:
        pass
    assert State.swap is original
