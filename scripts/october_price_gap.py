"""Offline October 10 21:14–22:05 UTC price-gap diagnostic, every third block."""
from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import crash_slices
import perf_native
from eval_quote_performance import check_report

from swaparch.collection_quotes import prepared_collection_context

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/october-price-gap'
REFERENCE = '88e6a0c2ddd26feeb64f039a2c41296fcb3f5640'


def main():
    OUT.mkdir(exist_ok=True)
    market = json.loads((ROOT / 'outputs/dense-crash/market-oracle-23549800-23550200.json').read_text())['rows']
    refs = {r['block']: r for r in json.loads((ROOT / 'outputs/dense-crash/references.json').read_text())['rows']}
    eligible = [r for r in market if '2025-10-10T21:14:00Z' <= r['timestamp'] <= '2025-10-10T22:05:00Z']
    selected = eligible[::3]
    if selected[-1] != eligible[-1]:
        selected.append(eligible[-1])
    loaded = perf_native.load_build(perf_native.DEFAULT_BUILD)
    rows = []
    for saved in selected:
        b = saved['block']
        ref = refs[b]
        context, annotation, client = prepared_collection_context(b)
        assert context.block.hash == ref['blockHash'] == saved['blockHash']
        assert client.network_requests == 0
        pool = next(s for s in context.states if REFERENCE in s.record.pool_id)
        assert pool.token0.symbol == 'USDC' and pool.token1.symbol == 'WETH'
        spot = 2**192 / pool.sqrt_price_x96**2 * 10**12
        feed = saved['oracleEvidence']
        eth = crash_slices.decode_round(feed['raw']['latestRoundData'], int(feed['raw']['decimals'], 16))
        usdc = crash_slices.decode_round(feed['quoteFeed']['raw']['latestRoundData'], int(feed['quoteFeed']['raw']['decimals'], 16))
        row = {'block': b, 'blockHash': ref['blockHash'], 'timestamp': saved['timestamp'],
               'v3Spot': spot, 'chainlink': saved['oraclePrice'],
               'aave': ref['aaveWethPrice'] / ref['aaveUsdcPrice'],
               'ethUsd': eth['price'], 'usdcUsd': usdc['price'],
               'ethFeedAgeSeconds': ref['timestamp'] - eth['updatedAt'],
               'usdcFeedAgeSeconds': ref['timestamp'] - usdc['updatedAt'],
               'ethRoundId': str(eth['roundId']), 'ethUpdatedAt': eth['updatedAt']}
        identity = crash_slices._fingerprint(annotation, 'benchmark-scopes-single-thread', loaded['manifest'])
        for size in (1, 10, 100):
            path = OUT / f'{b}-{size}.json.gz'
            if size == 100:
                path = ROOT / f'outputs/dense-crash/raw/{b}-WETH-USDC-100.json.gz'
                with gzip.open(path, 'rt') as f:
                    report = json.load(f)
            else:
                report = None
                if path.exists():
                    with gzip.open(path, 'rt') as f:
                        cached = json.load(f)
                    if cached['identity'] == identity:
                        report = cached['report']
                if report is None:
                    with perf_native.optimized(context, loaded):
                        _, report = crash_slices._run_quote(context, annotation, ('WETH', 'USDC', str(size)))
                    temp = path.with_suffix('.tmp')
                    with gzip.open(temp, 'wt') as f:
                        json.dump({'identity': identity, 'report': report}, f)
                    temp.replace(path)
            check_report(report)
            assert report['block_hash'] == ref['blockHash']
            assert report['request']['amount_in'] == size * 10**18
            for key, source in (('aggregated', 'best_split'), ('v3', 'saved_reference_pool')):
                result = report[source]
                assert result and result['feasible'] and result['residual_in'] == 0
                row[f'{key}{size}'] = result['amount_out'] / 10**6 / size
            row[f'report{size}'] = str(path.relative_to(ROOT))
        rows.append(row)
        print(json.dumps({'block': b, 'completed': len(rows), 'total': len(selected)}), flush=True)
    payload = {'requestedStart': '2025-10-10T21:14:00Z', 'requestedEnd': '2025-10-10T22:05:00Z',
               'generatedAt': datetime.now(UTC).isoformat(), 'stride': 3,
               'units': 'USDC per WETH; execution prices net of pool fees, excluding gas',
               'qualification': 'collection-model-only; bounded optimizer, incomplete public pool inventory, no historical RFQ',
               'v3ReferencePool': '0x' + REFERENCE, 'rows': rows}
    (OUT / 'prices.json').write_text(json.dumps(payload, indent=2) + '\n')
    with (OUT / 'prices.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    main()
