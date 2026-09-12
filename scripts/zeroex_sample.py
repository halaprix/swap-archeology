"""Bounded current WETH/USDC source sampling; never historical pool evidence."""
import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from zeroex_discovery import TOKENS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--rounds', type=int, default=24)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 120 or not 5 <= args.interval <= 300:
        parser.error('rounds must be 1..120 and interval 5..300 seconds')
    key = args.key_file.read_text().strip() if args.key_file else os.environ['ZERO_EX_API_KEY']
    session = requests.Session()
    session.headers.update({'0x-api-key': key, '0x-version': 'v2'})
    args.output.mkdir(parents=True, exist_ok=False)
    for index in range(args.rounds):
        start = time.monotonic()
        for size in (1, 10, 100, 1000):
            params = {'chainId': 1, 'sellToken': TOKENS['WETH'][0],
                      'buyToken': TOKENS['USDC'][0], 'sellAmount': str(size * 10**18)}
            record = {'round': index, 'sizeWeth': size, 'historical': False,
                      'requestedAt': datetime.now(UTC).isoformat(), 'params': params}
            try:
                response = session.get('https://api.0x.org/swap/allowance-holder/price',
                                       params=params, timeout=20)
                record['status'] = response.status_code
                record['response'] = response.json()
            except (requests.RequestException, ValueError) as exc:
                record['error'] = type(exc).__name__
            record['receivedAt'] = datetime.now(UTC).isoformat()
            (args.output / f'{index:03}-{size}.json').write_text(
                json.dumps(record, indent=2).replace(key, '[REDACTED]') + '\n')
            if record.get('status') in (401, 403, 429):
                raise SystemExit(f"Stopped on HTTP {record['status']}; no request burst/retry")
            time.sleep(0.15)
        print(f'round {index + 1}/{args.rounds}', flush=True)
        if index + 1 < args.rounds:
            time.sleep(max(0, args.interval - (time.monotonic() - start)))


if __name__ == '__main__':
    main()
