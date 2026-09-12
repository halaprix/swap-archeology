# Phase 9 backend bridge

`swaparch.web_api` is a loopback-only, stdlib HTTP service. `POST /quote` calls
the existing offline quote command under one non-waiting solver lock, with
baseline/search budgets capped at grid parts 4, four steps, beam width 24, and
300 expansions. A concurrent quote receives `busy` (HTTP 429). Token/source
field sizes and plain-decimal amount syntax are bounded before CLI parsing.
It resolves a supplied number through the cached header or a supplied hash
through an existing snapshot header. Missing headers/state are structured
`missing_state` errors; they never become a zero-output quote.

Results live in `web-data/quotes/`. The key includes the normalized request,
exact block hash, current inventory identity, snapshot identity, and identities
for every `src/swaparch` Python source, so edited solver/adapter/evaluator inputs
do not reuse a stale result. A report with no qualified selected pools returns
`qualification_required` (HTTP 409); a no-route report remains valid when at
least one selected source was qualified. Report integers are
projected with `explorer._display_report` and converted to strings with
`explorer._as_strings` before persistence or HTTP output.

`scripts/export_frontend_data.py` reads only real saved reports from the three
approved directories, validates them with explorer validators, retains the five
pinned blocks, and writes the static schema. It was intentionally not run while
the frontend scaffold is being created; use `--output` with a temporary path to
validate export without changing frontend files.

Validation run:

```text
pytest -q tests/test_web_api.py
python scripts/export_frontend_data.py --output /tmp/catalog.json
```
