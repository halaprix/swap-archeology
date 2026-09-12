# Dashboards and data

Run the Next.js frontend and start at `/`. All saved dashboards use bundled
exports under `frontend/public/`; they do not query Ethereum from the browser.

| Route | Question it answers |
| --- | --- |
| `/` | Dashboard entry point and saved routing reports |
| `/october-gap` | How did direct/aggregated ETH and WETH quotes compare with oracle references during the October window? |
| `/october-swap-events/index.html` | What prices did actual Uniswap V2/V3 pool legs execute at in the focused October interval? |
| `/crash-gaps` | How do the additional selected crash windows compare? |
| `/crash-slices` | What do the small pinned crash samples show? |
| `/quote-lab` | What does the offline router quote for qualified cached block state? Requires the Python service. |
| `/design-system` | Theme tokens and shared UI primitives for development |

## Three data layers

1. **Bundled presentation data**: roughly 51 MB of curated exports. Include these
   with the frontend to share saved charts; no multi-gigabyte archive is required.
2. **Qualified backend state**: discovery records, block headers, snapshots and
   cached calls. Needed for new hypothetical quotes at those blocks. Not included
   merely because a chart contains results for the same block.
3. **Raw research archive**: collection logs, intermediate outputs and replay
   evidence. Keep locally or distribute as an explicitly reviewed dataset, outside
   the ordinary source checkout. Existing files are not deleted by cleanup.

## October swap events

This is implemented in `scripts/october_swap_events.py`, with regression tests in
`tests/test_october_swap_events.py`. It is a bounded repeatable experiment, **not**
a registered source adapter or a general arbitrary-window event indexer.

- Scope: six direct WETH/USDC Uniswap V2/V3 pools, blocks 23550020–23550069
  (2025-10-10 21:30:23–21:40:23 UTC).
- Acquisition: `eth_getLogs`, pool token verification and block-hash checks.
- Analysis: pool-leg direction, raw integer amounts, per-block/minute/pool/size
  summaries, separate buy/sell VWAP (`sum(USDC) / sum(WETH)`).
- Execution ratios and post-swap spot prices are separate fields. Combining
  opposite directions does not yield an executable bid/ask spread.

The bundled result is already viewable at `/october-swap-events/index.html`, with
`data.json` and `trades.csv` next to it. To regenerate the fixed study:

```bash
uv run python scripts/october_swap_events.py
```

The script requires the bundled canonical October source series at
`frontend/public/five-crash-liquidity/crash-5/sources.json`. It validates and
reuses `outputs/october-swap-events/raw_logs.json` when present; otherwise it needs
your configured archive RPC. `--force-refresh` reacquires logs. It writes both
local analysis outputs and frontend exports, so regenerate deliberately. There is
no arbitrary start/end CLI yet; widening the study requires changing and validating
its scope rather than implying the existing 50-block result covers a full crash.

For general saved-route export, use `uv run python scripts/export_frontend_data.py`
only when the underlying saved reports are available. A clean checkout already
contains the selected exports and does not need to regenerate them to browse.
