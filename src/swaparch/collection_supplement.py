"""Merge explicitly selected, same-block supplemental snapshots into offline quotes."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import implemented_adapters, pool_record_from_json


def supplement_context(context, annotation: dict, root: Path):
    path = root / 'records' / f'{context.block.hash}.json'
    raw = path.read_bytes()
    document = json.loads(raw)
    snapshot_root = root / 'snapshots'
    snapshot = SnapshotStore(snapshot_root).load(context.block.chain, context.block.hash)
    if snapshot.block != context.block or document['blockHash'] != context.block.hash:
        raise ValueError('supplement block identity mismatch')
    records = tuple(pool_record_from_json(row) for row in document['records'])
    existing = {state.record.pool_id for state in context.states}
    if len({r.pool_id for r in records}) != len(records) or any(r.pool_id in existing for r in records):
        raise ValueError('duplicate supplemental pool capacity')
    adapters = implemented_adapters()
    states = tuple(adapters[r.family].load_state(r, snapshot) for r in records)
    ids = {r.pool_id for r in records}
    addresses = {(r.family, r.pool) for r in records}
    inventories = []
    for original in context.inventories:
        inventory = dict(original)
        matching = [row for row in document['records'] if row['family'] == inventory['family']]
        inventory['pools'] = [row for row in inventory['pools'] if row['pool_id'] not in ids] + matching
        if matching:
            inventory['status'] = 'supported'
        inventories.append(inventory)
    known_families = {inv['family'] for inv in inventories}
    new_families = {r['family'] for r in document['records']} - known_families
    for fam in sorted(new_families):
        matching = [row for row in document['records'] if row['family'] == fam]
        inventories.append({
            'family': fam,
            'chain': context.block.chain,
            'status': 'supported',
            'pools': matching,
        })
    merged = replace(context, states=context.states + states,
                     records=tuple(r for r in context.records if r.pool_id not in ids) + records,
                     inventories=tuple(inventories),
                     tokens={**context.tokens, **{t.address: t for r in records for t in r.tokens}},
                     unavailable=tuple(row for row in context.unavailable
                                       if (row.get('family'), row.get('pool')) not in addresses))
    directory = snapshot_root / str(context.block.chain) / context.block.hash
    digest = hashlib.sha256(raw + (directory / 'header.json').read_bytes()
                            + (directory / 'calls.json.gz').read_bytes()).hexdigest()
    annotation = dict(annotation)
    annotation['supplement_artifact'] = str(path)
    annotation['supplement_identity'] = digest
    artifacts = list(annotation.get('supplement_artifacts', []))
    artifacts.append(str(path))
    annotation['supplement_artifacts'] = artifacts
    identities = list(annotation.get('supplement_identities', []))
    identities.append(digest)
    annotation['supplement_identities'] = identities
    annotation['collection_evidence_identity'] = hashlib.sha256(
        (annotation['collection_evidence_identity'] + digest).encode()).hexdigest()
    return merged, annotation


def supplement_contexts(context, annotation: dict, roots: Iterable[Path]):
    """Apply multiple supplemental overlays in sequence."""
    for root in roots:
        rec_file = root / 'records' / f'{context.block.hash}.json'
        if rec_file.is_file():
            context, annotation = supplement_context(context, annotation, root)
    return context, annotation
