# Uniswap V3 CSV evidence fix

Status: V3 review finding 2 addressed offline on 2026-09-07.

The CSV driver now evaluates the exact-input, exact-output, and observed-terminal-price
models independently through `matching_models()`. It reports model match counts and overlap
sets. `classify()` remains a first-match compatibility API so the existing math replay check
keeps its historical labels; `limit` is documented as the observed-terminal compatibility
bucket, not a caller-mode claim.

The adapter documentation now describes the result as arithmetic consistency evidence. It
states that a `Swap` event omits the caller's signed amount, price limit, and call shape, and
that equal endpoint liquidity does not exclude intervening liquidity events. No claim is made
that the CSV reconstructs execution or proves the original caller mode.

Changed files:

- `scripts/uniswap_v3_csv_check.py`
- `tests/test_csv_evidence.py`
- `docs/adapters/uniswap_v3.md`
- `docs/reports/csv-evidence-fix.md`

The generated artifact was refreshed at
`data/adapters-evidence/uniswap_v3/swap-event-replay.json` from the three copied CSVs under
`evidence/`; `evidence/` was not changed.

Validation:

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline python scripts/uniswap_v3_csv_check.py
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest -q tests/test_csv_evidence.py tests/test_uniswap_v3_math.py
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline pytest -q
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline ruff check scripts/uniswap_v3_csv_check.py tests/test_csv_evidence.py
```

The replay command reported `4203` eligible pairs, with independent model counts of
`exact_in=3222`, `exact_out=899`, and `observed_terminal=4200`; overlap counts were
`exact_in+observed_terminal=3219`, `exact_out+observed_terminal=899`,
`observed_terminal=82`, and `exact_in=3`. The focused test command reported `19 passed`,
and Ruff reported `All checks passed!`. The full offline suite reported `51 passed, 1 skipped`.

The CSV contains historical event fields only. The overlap report cannot distinguish an
exact-input call with a binding price limit from another caller shape when both candidates fit,
and it does not prove that no liquidity event occurred between equal endpoint states.
