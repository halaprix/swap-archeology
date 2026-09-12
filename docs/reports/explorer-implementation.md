# Saved-result explorer implementation

`swaparch.explorer` builds one standalone HTML artifact from saved `swaparch quote`
JSON reports. It reads no RPC endpoint and does not alter saved reports or evidence.

```bash
uv run python -m swaparch.explorer data/results/litepsm-integration \
  --output data/explorer/litepsm-integration.html
```

The artifact accepts quote JSON files, directories, and shell-style globs. Directory
inputs intentionally skip `summary.json`; malformed quote reports fail the build.
Every integer becomes a JSON string before embedding and is displayed through
JavaScript `BigInt`, so 18-decimal raw quantities do not pass through `Number`.
The embedded projection keeps only a plan kind and recorded allocations, avoiding
dual-search runtime floats and diagnostic payloads that are not explorer data.
Report text is escaped before it enters the data script, and dynamic labels use text
nodes rather than report HTML.

The controls select supplied block/time, pair, direction, input size, and recorded
solver. Source selection stays disabled unless reports explicitly contain
`selected_families` from the CLI or `{"source_subset": {"recomputed": true,
"sources": [...]}}`; suppressing displayed route lines never claims to recompute a
source subset. Reports with no feasible `best_split` remain visible as a no-route
coverage gap. The current LitePSM directory
has one input size per block/direction, so the size/price chart says that no curve is
inferred. Outputs and gains are labelled pre-gas; supplied gas fields remain estimates.

Validation: `uv run pytest tests/test_explorer.py -q` builds search/dual-like and
subset reports, checks no-route admission, conflict rejection, script escaping, and
exact large integers. With Chrome available, `SWAPARCH_BROWSER_TEST=1 uv run pytest
tests/test_explorer.py -q` also changes controls in the rendered artifact and verifies
the 10% gain and a missing-block scenario gap.
