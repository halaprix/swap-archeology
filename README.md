# Swap Archeology

Historical Ethereum liquidity, routing and crash-price research. Compare modeled
sell quotes across selected AMMs/connectors with oracle references and observed
swap events. This is a research tool, not a transaction executor or a guarantee
of the best executable route.

## Explore the saved dashboards

Requires Node.js 20.9+ and npm. No Python, RPC account or archive mount is needed.

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. The frontend includes roughly 51 MB of curated JSON,
CSV and HTML exports. The multi-gigabyte raw archive is **not** needed to browse.
See [dashboard and data guide](docs/DASHBOARDS.md) and [frontend README](frontend/README.md).

## Run the Python tools

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run swaparch --help
uv run --extra benchmark pytest -m 'not rpc and not archive'
```

The test suite includes synthetic checks and optional historical-evidence checks.
Tests requiring excluded local artifacts must be reported separately from portable
checks; a clean checkout is not the complete historical archive.

For RPC reads, copy `.env.example` to `.env` and supply your own Ethereum archive
endpoint. Keep credentials out of reports and browser configuration. No RPC is
needed for the bundled frontend or synthetic tests.

For cached quotes, first supply discovery records, pinned snapshots and the RPC
cache for the requested block. See [running and data layout](docs/RUNNING.md).

```bash
uv run swaparch quote --block 23549939 --in WETH --out USDC --amount 1 --offline
```

This command requires the corresponding local state; it is not a fresh-checkout
demo. Missing state must fail explicitly rather than fetch silently or invent data.

## What is supported?

Nine adapter families are registered, with pool/version/size restrictions:
Uniswap V2/V3/V4, PancakeSwap V3, Fluid T1, selected Curve pools, Maker/Sky,
Lido wrapping and Origin Lido ARM. [Adapter support](docs/ADAPTERS.md) separates
implemented models from discovery-only sources and describes validation limits.
WBTC and USDS are connector tokens within that universe.

The October event study decodes actual V2/V3 Swap logs and computes separate
buy/sell volume-weighted prices. It does not quote hypothetical swaps or add
liquidity to the router. [How to reproduce it](docs/DASHBOARDS.md#october-swap-events).

## Development

```bash
uv run ruff check src tests scripts
uv run --extra benchmark pytest -m 'not rpc and not archive'
cd frontend
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

Core code lives in `src/swaparch/`; bounded acquisition/research entry points live
in `scripts/`; frontend code lives in `frontend/`. Older reports in `docs/reports/`
are dated evidence, not a current all-features completion claim. See
[publication checklist](docs/PUBLISHING.md) for the code/data boundary.

## License

Currently unlicensed, by owner choice. No open-source reuse license is granted.
Copied third-party material retains its original terms.

Automated GitHub checks are pending workflow-upload authorization. The commands
above were verified locally against the public file set.
