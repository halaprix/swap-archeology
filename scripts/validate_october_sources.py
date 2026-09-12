"""Check every-block chart coverage, units, missing values and direct-route bounds."""
import json
import math
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = json.loads((root/'outputs/october-sources/prices.json').read_text())
refs = {r['block']: r for r in json.loads((root/'outputs/dense-crash/references.json').read_text())['rows']}
rows = data['rows']
assert [r['block'] for r in rows] == list(range(23549939, 23550193))
assert len({r['blockHash'] for r in rows}) == 254
pools = {p['id']: p for p in data['pools']}
assert len(pools) == len(data['pools'])
families = {f['id'] for f in data['families']}
assert len(families) == 15
checks = 0
bounds = []
for row in rows:
    ref = refs[row['block']]
    assert row['blockHash'] == ref['blockHash']
    assert int(datetime.fromisoformat(row['timestamp']).timestamp()) == ref['timestamp']
    assert set(row['availability']) == families
    assert all(0 <= a['directCount'] <= a['usableCount'] for a in row['availability'].values())
    for pool_id, sizes in row['pools'].items():
        meta = pools[pool_id]
        for size in ('1', '10', '100'):
            quote = sizes[size]
            if quote['price'] is None:
                assert quote.get('reason'), (row['block'], pool_id, size)
                continue
            assert int(quote['amountOut']) > 0
            price = int(quote['amountOut']) / 10**6 / int(size)
            assert math.isclose(price, quote['price'], rel_tol=1e-12, abs_tol=1e-9)
            checks += 1
            aggregate = row['aggregates'][meta['inputSymbol']][size]
            if aggregate is not None and aggregate + 1e-8 < price:
                bounds.append({'block': row['block'], 'pool': pool_id, 'size': size,
                               'aggregate': aggregate, 'direct': price})
assert not bounds, f'Aggregate below direct-pool candidate: {bounds[:5]}'
result = {'blocks': len(rows), 'pools': len(pools), 'families': len(families),
          'positivePoolQuotesChecked': checks, 'directRouteBoundsPassed': True}
(root/'outputs/october-sources/validation.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))
