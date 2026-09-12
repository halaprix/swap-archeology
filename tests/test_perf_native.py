"""The optimized internal constructor must retain immutable snapshot boundaries."""
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace

import pytest

from swaparch.adapters.uniswap_v3.state import UniV3State
from swaparch.adapters.uniswap_v4.state import UniV4State
from swaparch.core.protocols import Unsupported


def test_successor_factory_validates_unknown_maps_and_ranges(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from perf_native import successor_constructors

    initial = UniV3State(None, 2**96, 0, 10**18, 500, 60, None, None, {0: 0}, {}, 0, 0)
    modules = {"perf_methods_v3": SimpleNamespace(UniV3State=UniV3State),
               "perf_methods_v4": SimpleNamespace(UniV4State=UniV4State)}
    with successor_constructors((initial,), {"modules": modules}):
        create = modules["perf_methods_v3"].UniV3State
        updated = create(**{**vars(initial), "liquidity": 2 * 10**18})
        assert updated.liquidity == 2 * 10**18
        assert updated.tick_bitmap is initial.tick_bitmap
        assert initial.liquidity == 10**18
        with pytest.raises(FrozenInstanceError):
            updated.liquidity = 0
        with pytest.raises(Unsupported, match="missing words"):
            create(**{**vars(initial), "word_hi": 1})
        backing = {0: 0}
        proxy = MappingProxyType(backing)
        copied = create(**{**vars(initial), "tick_bitmap": proxy})
        backing.clear()
        assert copied.tick_bitmap[0] == 0
        with pytest.raises(Unsupported, match="missing words"):
            create(**{**vars(initial), "tick_bitmap": proxy})
    assert modules["perf_methods_v3"].UniV3State is UniV3State


def test_loader_rejects_python_fallback_before_executing_it(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from perf_native import load_build

    (tmp_path / "manifest.json").write_text(json.dumps({"source_sha256": {}}))
    (tmp_path / "method_index.json").write_text("{}")
    (tmp_path / "perf_math_v3.py").write_text("raise RuntimeError('must not execute generated fallback')")
    with pytest.raises(ValueError, match="native extension missing"):
        load_build(tmp_path)
