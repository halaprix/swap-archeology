"""Collect known candidate state globally; discovery and quote qualification are separate."""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from eth_abi import decode, encode
from eth_utils import keccak

from swaparch.adapters.origin_arm import ARM as ORIGIN_ARM
from swaparch.adapters.origin_arm import SEL_GET_RESERVES, SEL_PAUSED
from swaparch.adapters.uniswap_v3 import UniswapV3Adapter
from swaparch.adapters.uniswap_v4 import UniswapV4Adapter
from swaparch.collection_metadata import CollectionMetadataAdapter
from swaparch.core.protocols import Unsupported
from swaparch.core.types import CallSpec, PoolRecord, SupportStatus
from swaparch.rpc.client import PROJECT_ROOT, RpcClient
from swaparch.snapshot.store import SnapshotStore
from swaparch.universe import (
    acquire,
    activation_reason,
    implemented_adapters,
    pool_record_from_json,
)

AAVE_POOL = '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2'
READ_PLAN_VERSION = 4


def _write_gzip(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(gzip.compress(json.dumps(payload, separators=(',', ':')).encode(), mtime=0))
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _call(address, signature, types=(), values=()):
    return CallSpec(address, '0x' + keccak(text=signature)[:4].hex()
                    + encode(types, values).hex(), signature)


def _retained_read_plan(requested_specs):
    return {key: spec for key, spec in requested_specs.items()
            if not (spec.to == ORIGIN_ARM and spec.data in {SEL_GET_RESERVES, SEL_PAUSED})
            and not spec.tag.startswith(('univ3:', 'univ4:'))}


def _retained_prefetch(report):
    """Reuse read identities, restoring Origin optionals only from its exact pinned record."""
    carried = [CallSpec(**row) for row in report.get('read_specs', [])]
    retained = [spec for spec in carried
                if not (spec.to == ORIGIN_ARM and spec.data in {SEL_GET_RESERVES, SEL_PAUSED})]
    block = report.get('block', {})
    block_hash = block.get('hash') if isinstance(block, dict) else None
    block_number = block.get('number') if isinstance(block, dict) else None
    for row in report.get('records', []):
        record = row.get('record') if isinstance(row, dict) else None
        if not isinstance(record, dict) or (
            record.get('family'), record.get('deployment'), record.get('pool'), record.get('pool_id')
        ) != ('origin_arm', ORIGIN_ARM, ORIGIN_ARM, f'origin_arm:{ORIGIN_ARM}:{ORIGIN_ARM}'):
            continue
        config = record.get('config')
        observations = config.get('historical_observations', {}) if isinstance(config, dict) else {}
        observation = observations.get(block_hash) if isinstance(observations, dict) else None
        if not isinstance(observation, dict) or observation.get('number') != block_number:
            continue
        if observation.get('get_reserves') is True:
            retained.append(CallSpec(ORIGIN_ARM, SEL_GET_RESERVES, 'origin_arm:getReserves'))
        if observation.get('paused_getter') is True:
            retained.append(CallSpec(ORIGIN_ARM, SEL_PAUSED, 'origin_arm:paused'))
    return list({(spec.to, spec.data): spec for spec in retained}.values())


class _AaveReference:
    family = 'aave_reference'

    def read_requests(self, pool, block):
        return [_call(pool.pool, 'ADDRESSES_PROVIDER()')]

    @staticmethod
    def _address(snapshot, call):
        result = snapshot.get(call)
        if not result.success:
            raise Unsupported(f'Aave identity read failed: {call.tag}')
        address = decode(['address'], bytes.fromhex(result.raw[2:]))[0]
        if int(address, 16) == 0:
            raise Unsupported('Aave identity is zero')
        return address

    def dependent_requests(self, pool, block, snapshot):
        provider = self._address(snapshot, self.read_requests(pool, block)[0])
        oracle_call = _call(provider, 'getPriceOracle()')
        if not snapshot.has(oracle_call):
            return [oracle_call]
        oracle = self._address(snapshot, oracle_call)
        return [_call(oracle, 'getAssetPrice(address)', ['address'], [token.address])
                for token in pool.tokens]

    def prices(self, pool, snapshot):
        rows = []
        for token, call in zip(pool.tokens, self.dependent_requests(pool, snapshot.block, snapshot), strict=True):
            result = snapshot.get(call)
            value = decode(['uint256'], bytes.fromhex(result.raw[2:]))[0] if result.success else None
            rows.append({'token': asdict(token), 'price_base': value,
                         'available': value is not None and value > 0})
        return rows


def collect_block(number: int, *, batch_size: int = 4000, offline: bool = False,
                  inventory_root: Path = PROJECT_ROOT / 'data/discovery/1',
                  client=None, store=None, output_root: Path | None = None,
                  previous_report: dict | None = None) -> dict:
    """Collect all known model-qualified candidates, without claiming fresh qualification.

    Snapshot identities are per block hash. Discovery is deliberately frozen for
    this pass; the report lists excluded identities and preserves its input digest.
    """
    started = time.monotonic()
    client = client if client is not None else RpcClient(offline=offline)
    store = store if store is not None else SnapshotStore(batch_size=batch_size)
    before = client.network_requests
    block = client.get_block(number)
    source_adapters = implemented_adapters()
    source_adapters['uniswap_v3'] = UniswapV3Adapter(bulk_ticks=True)
    source_adapters['uniswap_v4'] = UniswapV4Adapter(bulk_ticks=True)
    adapters = {key: CollectionMetadataAdapter(adapter)
                for key, adapter in source_adapters.items()}
    records, excluded, families = [], [], []
    inventories = {}
    digest = hashlib.sha256()
    for path in sorted(inventory_root.glob('*.json')):
        raw = path.read_bytes()
        digest.update(path.name.encode() + b'\0' + raw)
        inventory = json.loads(raw)
        inventories[path.name] = inventory
        family = inventory['family']
        families.append({'family': family, 'status': inventory.get('status'),
                         'unresolved': inventory.get('unresolved', []),
                         'adapter_implemented': family in adapters})
        for row in inventory.get('pools', []):
            record = pool_record_from_json(row)
            reason = activation_reason(record, number)
            if family not in adapters:
                reason = 'adapter not implemented'
            elif not record.config.get('validated_block_hashes'):
                reason = reason or 'no existing model qualification for this candidate'
            if reason:
                excluded.append({'family': family, 'pool_id': record.pool_id, 'reason': reason})
            else:
                records.append(record)
    tokens = {token.address: token for record in records for token in record.tokens
              if token.symbol in {'WETH', 'wstETH', 'sUSDe', 'USDC', 'USDT', 'DAI'}}
    # Existence verified at block 21762695 (Crash-1 start, code length 4802).
    # Evidence: data/discovery-evidence/activation/1/0x5e2b0ad801f51565cad7f363cf103be37b3b36b39deb2cae26cf6b12ea23f424/0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2.json
    reference = PoolRecord('aave_reference', 1, 'aave_reference', AAVE_POOL, AAVE_POOL,
                           tuple(tokens.values()), {}, None,
                           {'deployed_by_block': 21762695,
                            'evidence': ['data/discovery-evidence/activation/1/0x5e2b0ad801f51565cad7f363cf103be37b3b36b39deb2cae26cf6b12ea23f424/0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2.json']},
                           SupportStatus.SUPPORTED)
    aave = _AaveReference()
    adapters[aave.family] = aave
    prefetch = []
    if (previous_report and previous_report['block']['chain'] == block.chain
            and previous_report.get('read_plan_version') == READ_PLAN_VERSION
            and previous_report['block']['number'] < block.number
            and previous_report['inventory_identity'] == digest.hexdigest()
            and len(previous_report.get('read_specs', [])) <= 3 * batch_size):
        # Reuse only request identities: every value is freshly read at the new hash.
        # ponytail: reset above three batches to bound accumulated stale tick hints.
        prefetch = _retained_prefetch(previous_report)
    snapshot, acquisition = acquire(adapters, [*records, reference], block, store, client,
                                    prefetch_specs=prefetch)
    collected = []
    for record in records:
        try:
            if record.pool_id in acquisition.unsupported:
                raise Unsupported(acquisition.unsupported[record.pool_id])
            state = adapters[record.family].load_state(record, snapshot)
            saved = asdict(state.record)
            config = saved['config']
            for key in ('registry_observations', 'historical_observations', 'resolver_observations'):
                if key in config:
                    config[key] = {block.hash: config[key][block.hash]} if block.hash in config[key] else {}
            config['validated_block_hashes'] = ([block.hash] if block.hash in config.get('validated_block_hashes', []) else [])
            collected.append({'record': saved,
                              'quote_qualified_here': block.hash in record.config.get('validated_block_hashes', [])})
        except (Unsupported, KeyError, ValueError) as error:
            excluded.append({'family': record.family, 'pool_id': record.pool_id, 'reason': str(error)})
    try:
        prices = aave.prices(reference, snapshot)
    except (Unsupported, KeyError, ValueError) as error:
        prices = []
        excluded.append({'family': aave.family, 'pool_id': reference.pool_id, 'reason': str(error)})
    # Rebuild tick requests from the current bitmap, dropping obsolete arrays/windows.
    plan = _retained_read_plan(store.requested_specs)
    for record in records:
        if record.family not in {'uniswap_v3', 'uniswap_v4'} or record.pool_id in acquisition.unsupported:
            continue
        try:
            specs = source_adapters[record.family].prefetch_requests(record, block, snapshot)
            plan.update({(spec.to, spec.data): spec for spec in specs})
        except (Unsupported, KeyError, ValueError):
            pass  # Its initial reads are retried normally on the next block.
    report = {'block': asdict(block), 'mode': 'state_collection',
              'read_plan_version': READ_PLAN_VERSION,
              'inventory_identity': digest.hexdigest(), 'discovery_refreshed': False,
              'qualification_performed': False, 'families': families,
              'records': collected, 'excluded': excluded, 'aave_prices': prices,
              'snapshot': str(store.root / str(block.chain) / block.hash),
              'batch_size': batch_size, 'waves': store.acquisition_batches,
              'read_specs': [asdict(spec) for spec in plan.values()],
              'prefetched_calls': len(prefetch),
              'network_requests': client.network_requests - before,
              'elapsed_seconds': time.monotonic() - started}
    output_root = output_root if output_root is not None else PROJECT_ROOT / 'data/collection'
    inventory_archive = output_root / 'inventories' / f'{digest.hexdigest()}.json.gz'
    if not inventory_archive.exists():
        _write_gzip(inventory_archive, inventories)
    report['inventory_evidence'] = str(inventory_archive)
    output = output_root / str(block.chain) / f'{block.hash}.json.gz'
    _write_gzip(output, report)
    return {**report, 'evidence': str(output)}
