# Running and data layout

## Saved dashboards: smallest setup

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. For a production build, run `npm run build` and
`npm start`. This mode uses only bundled exports; leave `SWAPARCH_API_URL` unset.

## Python installation

From the source checkout, with Python 3.12+ and uv installed:

```bash
uv sync --locked
uv run swaparch --help
uv run swaparch-web --help
```

A wheel contains code, not the historical archive. Set `SWAPARCH_ROOT` when using
an installed package or a prepared dataset outside the checkout:

```text
prepared-root/
  data/
    discovery/1/               # source inventories and identities
    snapshots/1/<block-hash>/   # headers and source read state
    rpc-cache/                 # cached pinned RPC responses
```

`SWAPARCH_ROOT` points to `prepared-root`, not its `data` child. Existing source
checkouts default to their project root. A chart export cannot substitute for
these inputs: it contains results, not all state needed to evaluate another quote.

```bash
export SWAPARCH_ROOT=/path/to/prepared-root
uv run swaparch quote --block 23549939 --in WETH --out USDC --amount 1 --offline
```

Use actual qualified state for the requested block. `--offline` fails on missing
inputs and never requests RPC data. Solver budgets and source restrictions remain
part of the result; a feasible route is not proof of global optimality.

## Optional Quote Lab backend

```bash
export SWAPARCH_ROOT=/path/to/prepared-root
uv run swaparch-web --host 127.0.0.1 --port 8765 --data-root /path/to/quote-results
```

Then start the frontend in a separate terminal:

```bash
cd frontend
SWAPARCH_API_URL=http://127.0.0.1:8765 npm run dev
```

`SWAPARCH_ROOT` selects historical input state. `--data-root` selects storage for
new ad hoc reports; these are different directories with different purposes.
The server binds locally by default. Publishing source does not expose this server.
For public hosting, provide authentication/request limits before enabling the
quote proxy; do not expose RPC credentials to the browser.

## Acquisition and regeneration

Supply your own archive Ethereum RPC via `ETH_RPC_URL` or `RPC_MAINNET`, or copy
`.env.example` to `.env`. An external env file is used only when explicitly named
with `SWAPARCH_ENV_FILE`; no other project's credentials are loaded implicitly.

The scripts directory contains bounded research commands, not one automatic
all-protocol backfill. Inspect each command's scope before running it. For example:

```bash
uv run swaparch header 23550044
uv run python scripts/october_swap_events.py --help
```

The first command needs a cached response or RPC; the second only shows usage.
[Dashboard regeneration](DASHBOARDS.md#october-swap-events) explains the fixed
October event study. No cleanup command restarts collectors or sweeps.

## Tests

Portable CI excludes network and optional historical archive checks. The
`benchmark` extra installs SciPy for the optimizer-specific tests; normal
saved browsing and baseline quoting do not require it:

```bash
uv run ruff check src tests scripts
uv run --extra benchmark pytest -m 'not rpc and not archive'
```

With the original local research artifacts available, also run:

```bash
uv run --extra benchmark pytest -m 'not rpc'
```

Archive and RPC exclusions are coverage limits, not successful historical replay.
The frontend checks are independent of the backend archive:

```bash
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

The LitePSM source-qualification script additionally requires the pinned Solidity
source at `data/protocol-sources/litepsm/DssLitePsm.sol`, or an explicit
`SWAPARCH_LITEPSM_SOURCE` path. It verifies the expected source hash before RPC
use; this local source artifact is not bundled with the code.
