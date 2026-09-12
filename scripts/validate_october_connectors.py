"""Validate the bounded connector export and explicitly accounted PSM refunds."""
import gzip
import json
from pathlib import Path

from eval_quote_performance import check_report

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/october-sources-connectors'
DAI = '0x6b175474e89094c44da98b954eedeac495271d0f'
PSM = 'maker_sky_psm:0xf6e72db5454dd049d0788e411b06cfaf16853042:0xf6e72db5454dd049d0788e411b06cfaf16853042'
THREEPOOL = '0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7'
data = json.loads((OUT / 'prices.json').read_text())
previous = {r['block']: r for r in json.loads((ROOT / 'outputs/october-sources/prices.json').read_text())['rows']}
assert [r['block'] for r in data['rows']] == list(range(23549939, 23550193))
improved = psm = curve = cases = refunds = 0
worse = []
max_improvement = {'bps': 0}
for row in data['rows']:
    old = previous[row['block']]
    assert row['blockHash'] == old['blockHash'] and row['timestamp'] == old['timestamp']
    assert row['pools'] == old['pools']
    assert row['chainlink'] == old['chainlink'] and row['aave'] == old['aave']
    assert row['availability']['curve']['usableCount'] == old['availability']['curve']['usableCount'] + 1
    with gzip.open(ROOT / row['rawIndex']['aggregates'], 'rt') as handle:
        raw = json.load(handle)
    assert raw['blockHash'] == row['blockHash']
    assert len(raw['reports']) == 6
    for key, report in raw['reports'].items():
        asset, size = key.split(':')
        check_report(report)
        best = report['best_split']
        assert report['request']['allow_psm_dai_refund'] is True
        assert best['feasible'] and best['residual_in'] == 0
        assert best['amount_in_spent'] == int(size) * 10**18
        assert abs(best['amount_out'] / 1e6 / int(size) - row['aggregates'][asset][size]) < 1e-8
        refund = best.get('terminal_refund', {})
        assert row['aggregateRefunds'][asset][size] == refund
        if refund:
            assert set(refund) == {DAI} and 0 < refund[DAI] < 10**12
            witnesses = [(i,s) for i,s in enumerate(best['steps']) if s['pool_id'] == PSM and s['token_in'] == DAI]
            assert len(witnesses) == 1
            index, step = witnesses[0]
            assert step['amount_in'] > 0
            assert not any(s['token_out'] == DAI for s in best['steps'][index+1:])
            refunds += 1
        psm += any(s['pool_id'] == PSM for s in best['steps'])
        curve += any(s['pool_id'].endswith(THREEPOOL) for s in best['steps'])
        price = row['aggregates'][asset][size]
        before = old['aggregates'][asset][size]
        bps = (price / before - 1) * 10000
        if price + 1e-8 < before:
            worse.append({'block': row['block'], 'case': key, 'bps': bps})
        improved += price > before + 1e-8
        if bps > max_improvement['bps']:
            max_improvement = {'block': row['block'], 'case': key, 'bps': bps, 'before': before, 'after': price}
        cases += 1
result = {'blocks': len(data['rows']), 'cases': cases, 'improved': improved,
          'psmRoutes': psm, 'threepoolRoutes': curve, 'explicitDustRefunds': refunds,
          'maxImprovement': max_improvement, 'regressions': worse}
(OUT / 'validation.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))
assert not worse, 'Investigate search regressions before publishing'
