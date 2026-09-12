"""Lookup indexing preserves call identity and immutable snapshot semantics."""
import pytest

from swaparch.core.types import BlockRef, CallResult, CallSpec
from swaparch.snapshot.store import StoredSnapshot


def test_index_ignores_tags_and_preserves_missing_and_duplicate_behavior():
    spec = CallSpec('0x' + '01' * 20, '0xab', 'first')
    first = CallResult(spec, True, '0x01')
    duplicate = CallResult(CallSpec(spec.to, spec.data, 'other'), True, '0x02')
    snapshot = StoredSnapshot(BlockRef(1, 1, '0xabc', 0), (first, duplicate))
    assert snapshot.get(duplicate.spec) is first
    assert snapshot.has(CallSpec(spec.to, spec.data))
    missing = CallSpec(spec.to, '0xcd')
    assert not snapshot.has(missing)
    with pytest.raises(KeyError):
        snapshot.get(missing)
    with pytest.raises(TypeError):
        snapshot._calls_by_key[(spec.to, spec.data)] = duplicate
