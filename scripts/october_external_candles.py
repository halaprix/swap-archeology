import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests

out=Path('outputs/october-validation');out.mkdir(exist_ok=True)
results=[]
for symbol in ['ETHUSDC','ETHUSDT']:
 name=f'{symbol}-1m-2025-10-10.zip'
 url=f'https://data.binance.vision/data/spot/daily/klines/{symbol}/1m/{name}'
 r=requests.get(url,timeout=30);r.raise_for_status()
 checksum=requests.get(url+'.CHECKSUM',timeout=30);checksum.raise_for_status()
 assert hashlib.sha256(r.content).hexdigest()==checksum.text.split()[0]
 (out/name).write_bytes(r.content)
 (out/(name+'.CHECKSUM')).write_text(checksum.text)
 with zipfile.ZipFile(io.BytesIO(r.content)) as z:
  rows=list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
 selected=[]
 for row in rows:
  raw=int(row[0]);ts=raw/(1e6 if raw>1e14 else 1e3)
  stamp=datetime.fromtimestamp(ts,UTC).isoformat()
  if '2025-10-10T21:14:00'<=stamp<='2025-10-10T22:05:00+00:00':
   selected.append({'timestamp':stamp,'open':float(row[1]),'high':float(row[2]),'low':float(row[3]),'close':float(row[4]),'baseVolume':float(row[5]),'trades':int(row[8])})
 assert len(selected)==52 and all(x['low']<=min(x['open'],x['close'])<=max(x['open'],x['close'])<=x['high'] for x in selected)
 results.append({'symbol':symbol,'source':url,'checksumVerified':True,'rows':selected})
(out/'external-candles.json').write_text(json.dumps(results,indent=2)+'\n')
for series in results:
 print(series['symbol'],len(series['rows']))
 for row in series['rows']:
  if any(t in row['timestamp'] for t in ['21:30:00','21:35:00','21:36:00']):print(row)
