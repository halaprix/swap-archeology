# Swap Archeology frontend

Next.js 16.3.4 / React 19.2.8 research app. The dashboard starts with bundled
historical data, including saved reports, charts, CSV exports, and the October
swap-event diagnostic. The bundle is roughly 51 MB; browsing it does not require
the multi-gigabyte research archive or a Python service.

## Browse saved dashboards

From this directory:

```bash
npm ci
npm run dev
```

Open http://localhost:3000. Leave `SWAPARCH_API_URL` unset for this static-data
mode. See [the dashboard data guide](../docs/DASHBOARDS.md) for the data layers,
coverage, and regeneration boundaries.

## Optional Quote Lab backend

Quote Lab evaluates an ad hoc request only against qualified, cached historical
state. It does not acquire new blocks or make browser RPC calls. From the
repository root, start the local bridge:

```bash
uv run swaparch-web --port 8765
```

Then start the frontend with its server-only bridge URL:

```bash
cd frontend
SWAPARCH_API_URL=http://127.0.0.1:8765 npm run dev
```

For a prepared dataset outside the checkout, `SWAPARCH_ROOT` identifies the
input root containing `data/`, while `--data-root` is separate storage for new
ad hoc quote results:

```bash
export SWAPARCH_ROOT=/path/to/prepared-root
uv run swaparch-web --port 8765 --data-root /path/to/quote-results
```

Missing or unqualified state returns an explicit error. Baseline and search are
bounded heuristics; displayed outputs exclude gas and make no optimality claim.

## Pages and styling

- `/`: dashboard index and saved-route explorer with pinned reports,
  pair/amount/solver filters, ordered route flow, exact step amounts, historical
  observations, and actual source coverage.
- `/october-gap`: 254-block direct source comparison from October 10, 2025,
  with oracle-reference series and strict block/hash alignment checks.
- `/october-swap-events/index.html`: standalone, decoded event diagnostic for
  six direct WETH/USDC pools across 50 October blocks. It is not an overlay on
  the October quote chart; `data.json` and `trades.csv` sit alongside it.
- `/crash-gaps`: direct-liquidity comparisons across five selected crash windows,
  with each window's collection status and coverage shown in the UI.
- `/crash-slices`: 48 saved multi-source routing observations at 16 crash blocks,
  compared with same-block Chainlink cross-rates and direct-pool baselines.
- `/quote-lab`: token in/out, decimal amount, block number/hash, solver and sources;
  saved ad hoc results reopen when the optional local bridge is configured.
- `/design-system`: secondary theme and component gallery.

Edit CSS custom properties in `src/app/globals.css` and shared primitives in
`src/components/ui/`. See [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md). Version-matched
Next.js documentation is referenced by [AGENTS.md](AGENTS.md).

## Validate and refresh data

```bash
npm test
npm run typecheck
npm run lint
npm run build
```

From the repository root, `uv run python scripts/export_frontend_data.py`
refreshes `frontend/public/data/catalog.json` from saved reports. The exporter
keeps exact integer strings, hashes and source identities. Do not copy the raw
RPC cache or credentials into the frontend.

## Future Vercel deployment

Use `frontend` as the project root and the Next.js preset (`npm run build`).
Saved-report browsing needs only the bundled catalog, no Python or archive mount.
Ad hoc quotes additionally need a separately hosted Python service with its
qualified snapshots. Set its URL in the **server-only** `SWAPARCH_API_URL` variable;
localhost will not reach this workstation from Vercel. Hosting, access controls,
and deployment validation remain a separate task. Nothing has been deployed.
