"""Export compact chart/download data; retain full diagnostics in research outputs."""
import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = json.loads((root/'outputs/october-eth-prices/prices.json').read_text())
keys = ['block', 'blockHash', 'timestamp', 'chainlink', 'aave',
        'eth1', 'eth10', 'eth100', 'weth1', 'weth10', 'weth100']
rows = [{k: r[k] for k in keys} for r in source['rows']]
assert len(rows) == 86 and len({r['blockHash'] for r in rows}) == 86
payload = {k: v for k, v in source.items() if k != 'rows'}
payload['rows'] = rows
(root/'frontend/public/october-eth-prices.json').write_text(json.dumps(payload)+'\n')
with (root/'frontend/public/october-eth-prices.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=keys)
    writer.writeheader()
    writer.writerows(rows)
