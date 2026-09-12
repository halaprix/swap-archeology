# LitePSM finite local support

Implemented 2026-09-08 as a source-specific helper and integrated into the
numerical dual guide. It supplies a local objective and an exact integer action
only for a positive endpoint; `DualSolver` still sends every candidate through
stateful search and the fresh evaluator. It is a numerical estimate, never a
route bound or a claim that LitePSM can be independently combined with a
wrapper using the same inventory.

For USDC to DAI, the continuous local rate is
`factor * (1 - tin / 1e18)`. The endpoint is the largest positive-output USDC
exact input accepted by the current `dai_buffer`, located with the immutable
state's exact quote and replayed before it becomes an integer seed. `tin ==
HALTED` disables the direction; `tin == 1e18` has a zero output rate and emits
no endpoint rather than searching an economically meaningless uint256 range.

For DAI to USDC, the helper optimizes in USDC output `g`, bounded by the largest
exactly replayable prefix below `min(pocket_gem, pocket_gem_allowance)`. The
prefix search includes the source's checked gross multiplication, checked fee
multiplication, checked DAI-buffer addition, inventory and allowance. Its
continuous DAI cost is
`factor * (1 + tout / 1e18) * g`. The executable input is not an arbitrary
DAI amount: it is the source lattice
`required_dai(g) = factor*g + floor(factor*g*tout/1e18)`. The helper computes
the endpoint's required DAI with the state method and replays the quote. This
preserves the adapter's residual rejection and allowance consumption semantics.

Both directions return zero, endpoint, or tie support. Negative endpoint
economics and ties select zero flow/objective; separate endpoint diagnostics
record the actual replay without turning it into a recovery action. At equality,
diagnostics retain the full finite tie interval and deliberately provide no
recovery seed. Each result records the directional variable, finite capacity,
rounding/lattice information, exact replay status, and
`numerical_estimate_not_a_bound` status. The helper models one direction of one
LitePSM state at a time. Repeated/reverse visits and aliases sharing the PSM
remain outside its containment claim and must use the exact stateful evaluator.

Offline checks cover nonzero sell/buy fees; sell capacity `C-1/C/C+1`; buy
output capacity and the DAI lattice around `required(g)`; directional halts;
100% sell fee; tie intervals; gross/fee/DAI-addition overflow prefixes; and
exact endpoint replay at all five canonical cached hashes. The independent
reviewer's closed-form reference passed 270 cases over 48 synthetic states and
five cached pins. The other inventory rows remain explicit exclusions when
loading the canonical LitePSM discovery file.

```text
UV_CACHE_DIR=/tmp/swaparch-uv-cache uv run --offline --extra benchmark \
  pytest -q
# 173 passed, 2 skipped
```

The all-family cached WETH→USDC 1-WETH smoke at block 25896003 is saved in
`data/results/psm-integration/all-family-dual-1weth-usdc-25896003.json`.
It admits the LitePSM model and returns 21 feasible dual candidates with the
fresh evaluator; its winner is a direct 2,395.804405-USDC route, so it is an
admission/fallback smoke rather than evidence of a PSM allocation. LitePSM-only
buy and sell smokes are under `data/results/litepsm-dual-smoke/`.
