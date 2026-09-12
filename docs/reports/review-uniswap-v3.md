# Independent review: Uniswap V3

## Verdict: supported-with-caveats

The **recorded WETH/USDC 500-pip baseline, loaded through `UniswapV3Adapter.load_state`, is supported by the offline evidence**. I reproduced all 18 cached QuoterV2 output/terminal-price comparisons and nine saved funded-swap outputs. An independent integer calculation for 1 WETH at block **23549991** gives **3354316759 raw USDC**, exactly matching the local adapter and the recorded QuoterV2 response.

This is **not a completed fresh-RPC acceptance check**: all four cast invocations failed at DNS resolution, with zero successful RPC responses. No number below is represented as a fresh cast result. The broader claims that all 52 pools are supported, that missing state always produces `Unsupported`, and that the CSV labels establish the original caller's swap mode do not hold as stated.

Review scope: requirements and raw evidence first, then implementation/tests/reports; historical reads only. Only this report was written. No git commands, repository edits outside this report, new snapshots, or evidence rewrites. Python bytecode and pytest cache writes were disabled. Mutations were confined to process memory.

## Ranked findings

### 1. High — a missing bitmap word inside the advertised range becomes fictitious empty liquidity

**Locations:** `src/swaparch/adapters/uniswap_v3/state.py:109–115`, `scripts/uniswap_v3_window_analysis.py:38–78`.

`_word()` checks the numerical window, then uses `self.tick_bitmap.get(word_pos, 0)`. Thus an absent word inside that window becomes a zero bitmap. `UniV3State.__post_init__` freezes maps but does not validate their completeness. The window-analysis builder drops unsuccessful reads and derives coverage from `min(bitmap), max(bitmap)`, so it can construct precisely this invalid state.

**Reproduced input:** copy the block-23549991 radius-8 fixture **in memory**, mark only `univ3:tickBitmap:76` unsuccessful, and call `uniswap_v3_window_analysis.build_state`. The resulting state still advertises `[68,84]`. WETH→USDC quotes are:

| Input | Intact snapshot, raw USDC | Missing word 76, raw USDC |
|---:|---:|---:|
| 10 WETH | 33513282505 | 33513283344 |
| 100 WETH | 332132193469 | 332173436295 |
| 1000 WETH | 3002144531446 | 3052208485530 |

No `Unsupported` is raised by that state. This contradicts the hard no-extrapolation guarantee.

**Scope qualification:** the normal `UniswapV3Adapter.load_state` rejects this same missing-current-word fixture with `Unsupported: tickBitmap word 76 (the current word) was not loaded`; `_contiguous_range` also excludes holes outside the current word. Both saved radius-8 snapshots are complete and successful (1420/1420 and 1484/1484 reads). I did not find this corruption in them. Fix the shared state boundary so public construction and auxiliary builders cannot bypass the guarantee.

### 2. Medium — CSV classification is selected after comparison; caller-mode claims are unproven

**Locations:** `scripts/uniswap_v3_csv_check.py:9–30,65–82`; `tests/test_uniswap_v3_math.py:217–237`; `docs/adapters/uniswap_v3.md:123–154`.

The actual rule is first matching candidate: try exact-input, then exact-output, then use the **observed terminal price as the target**. The script states this ordering, but it does not assign a mode independently before comparing the predicted result. It therefore fails task C's independence requirement.

My separate integer formulas reproduce the counts, but also expose ambiguity: **3219 pairs match both exact-input and the observed-price target; all 899 labeled exact-output match the observed-price target too.** Only three pairs match exact-input alone; 82 match only the observed-price target among these candidates. See the examples and equations below.

Consequently, “981 were not exact-input calls” is too strong. What is established is that 981 fail this **single-step, uncapped, observed-consumed-input** model. An actual exact-input swap with a binding price limit is itself an exact-input call, and its original specified amount is absent from the CSV. Events do not contain the signed `amountSpecified` or caller's price limit.

The eligibility rationale is also too strong: equal endpoint liquidity does not logically exclude intervening mint/burn operations, zero-net initialized ticks, or crossings whose liquidity changes cancel. Those events are not supplied in these CSVs. The 4203 matches are useful arithmetic consistency evidence, not a reconstructed execution trace.

### 3. Medium — every discovered pool is promoted to supported without its own state/semantics validation

**Locations:** `scripts/uniswap_v3_discovery_run.py:105–118,167–195`; `data/discovery/1/uniswap_v3.json:3551–3567`; shared status meaning in `src/swaparch/core/types.py:74`.

The runner passes `SupportStatus.SUPPORTED` unconditionally when constructing records. All **52** records have that status, despite the inventory explicitly acknowledging that only one pool was cross-checked and that stETH pools were not depth-verified. Five discovered pools contain stETH and are marked supported. Concrete example: **0xf25b9f70f7e1d9a2968b42de00efda666efb2de3**.

The same factory/math is useful family-level evidence; it does not establish historical usable liquidity or token-transfer behavior for every record. `load_state` matches token addresses but has no token-semantics eligibility gate. Discovery identity and validated route availability should remain distinguishable. I did **not** observe a failed settlement for one of these pools; the finding is an unsupported status promotion, not a fabricated execution failure.

### 4. Medium — a failed bitmap subcall crashes dependency planning instead of yielding a classified unsupported source

**Location:** `src/swaparch/adapters/uniswap_v3/adapter.py:168–177,271–276`.

Using the in-memory failed-word-76 fixture from finding 1, `dependent_requests` sees that the call exists, so does not request it as missing. `_decoded` returns `None` for `success=False`; `abi_decode(["uint256"], None)` then raises:

```text
TypeError: The `data` value must be of bytes type. Got <class 'NoneType'>
```

This is a concrete acquisition failure under the required `allowFailure=true` model. There is no extrapolated output on this path, but it is not the promised `Unsupported` classification either. Failure handling is absent from the existing state tests.

### 5. Low — the amount0 overflow fallback omits Solidity's checked addition

**Location:** `src/swaparch/adapters/uniswap_v3/math.py:234`; reference `SqrtPriceMath.sol:47` and `LowGasSafeMath.sol:12–13` in the local v3-core checkout.

Executed input:

```python
m.get_next_sqrt_price_from_input(1 << 96, 1, (1 << 256) - 1, True)
# observed Python result: 1
```

The multiplication overflows the uint256 fast path. In the fallback, `(numerator1 // sqrtPX96) + amount` is `1 + (2**256 - 1)`. Solidity uses `.add(amount)` and reverts on that overflow. Python instead uses the unbounded denominator `2**256` and returns 1.

This disproves the module's universal “bit for bit” claim. It does **not** explain any baseline quote mismatch: this input is outside the positive int256 domain of `Pool.swap`, although it is a valid uint256 input to the SqrtPriceMath helper. Restore the checked-add semantics rather than wrapping the denominator.

### 6. Low — the online acquisition helper records a hash but executes by number without a final hash check

**Locations:** `scripts/uniswap_v3_online_check.py:95–103,133`; `scripts/castlib.py:79–83,111–127`.

The recorded hash is read before acquisition; subsequent chunks use the block number and there is no after-check. This falls short of `docs/INTERFACES.md`'s hash-pinning or before/after verification rule. `castlib.multicall` also uses fixed chunks and has no adaptive retry or individual fallback for failed subcalls.

These are observed implementation gaps. I observed **no reorganization or mixed snapshot**. The old block hashes and raw slot0 responses agree across the supplied files.

## Independent recomputation and RPC outcome

### Exact cast commands attempted

Working directory: `<checkout>`. These were issued in two shell invocations, each loading the environment as shown. The first command requests the raw `slot0()` response; the other three are the required liquidity and QuoterV2 reads. No online acquisition script was run because it writes files outside this report.

```bash
set +x
set -a
. ./.env
set +a
cast rpc --rpc-url "$ETH_RPC_URL" eth_call \
  '{"to":"0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640","data":"0x3850c7bd"}' \
  0x1675827 2> >(sed -E 's#https?://[^ "]+#<rpc-url>#g' >&2)
```

```bash
set +x
set -a
. ./.env
set +a
POOL=0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
QUOTER=0x61fFE014bA17989E743c5F6cB21bF9697530B21e
WETH=0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2
USDC=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
cast call "$POOL" 'liquidity()(uint128)' --block 23549991 \
  --rpc-url "$ETH_RPC_URL" \
  2> >(sed -E 's#https?://[^ "]+#<rpc-url>#g' >&2)
for amount in 1000000000000000000 1000000000000000000000; do
  cast call "$QUOTER" \
    'quoteExactInputSingle((address,address,uint256,uint24,uint160))(uint256,uint160,uint32,uint256)' \
    "($WETH,$USDC,$amount,500,0)" --block 23549991 \
    --rpc-url "$ETH_RPC_URL" \
    2> >(sed -E 's#https?://[^ "]+#<rpc-url>#g' >&2)
done
```

The raw slot0 command exited 1:

```text
Error: MPP HTTP request to <rpc-url> failed: HTTP request failed: error sending request: error sending request: client error (Connect): dns error: failed to lookup address information: Temporary failure in name resolution
```

Each `cast call` also failed, beginning `Error: failed to retrieve chain ID from fork endpoint`, followed by the same redacted DNS failure. These did not reach their intended `eth_call` stage. **Budget: four cast invocations attempted, zero successful RPC responses; no further RPC attempts.** There are no fresh slot0, liquidity, or QuoterV2 numbers to report. DNS failed before a connection could reach the provider.

### Cached raw evidence, independently decoded and locally recomputed

Sources: `snapshot-23549991-r8.json`, `quoter-cross-check-23549991-r8.json` under `data/adapters-evidence/uniswap_v3/`, and `evidence/crash-rescue-simulation/standing-prices.json`.

| Field | Observed cached value |
|---|---|
| Chain / block | 1 / 23549991 |
| Block hash | `0x0a9587d329fd6a15fed12b07631855b999c713e9ec43b19eb9fa75c6f2108623` |
| Pool | `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640` |
| sqrtPriceX96 | 1367563279517029399426676730054431 |
| tick | 195133 |
| liquidity | 584371440477331545 |
| fee / tick spacing | 500 / 10 |
| Remaining slot0 fields | observationIndex=120, cardinality=723, cardinalityNext=723, feeProtocol=0, unlocked=true |

| WETH input, raw | Recomputed local USDC, raw | Recorded QuoterV2 USDC, raw | Local and recorded terminal sqrtPriceX96 |
|---:|---:|---:|---:|
| 1000000000000000000 | 3354316759 | 3354316759 | 1367698790157946970110541127921282 |
| 1000000000000000000000 | 3002144531446 | 3002144531446 | 1543793104370873239424175440215232 |

Both output differences and terminal-price differences are zero. Decimal outputs are **3354.316759 USDC** and **3002144.531446 USDC**. Recorded QuoterV2 return fields also contain initialized-tick counts **1 / 216** and gas estimates **80127 / 5262928** respectively. These are old QuoterV2 simulation fields, not new measurements or the adapter's flat 120000 label. The 1-WETH local price ends before the next initialized tick; the recorded count must not be read as proof of one liquidity-changing crossing.

The two recorded raw QuoterV2 return byte strings, decoded directly rather than trusting their adjacent JSON number fields, are:

```text
1 WETH:
0x00000000000000000000000000000000000000000000000000000000c7eecfd7000000000000000000000000000000000000436ec92307e81b95b5daf272ae82000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000138ff
1000 WETH:
0x000000000000000000000000000000000000000000000000000002bafdc223f60000000000000000000000000000000000004c1d688dba8817c549c247053cc000000000000000000000000000000000000000000000000000000000000000d80000000000000000000000000000000000000000000000000000000000504e50
```

### Independent single-step arithmetic for 1 WETH

WETH is token1. Let `Q=2**96`, `P` be initial sqrtPriceX96, and `L` initial liquidity. The fee-discounted input is `N=floor(I*999500/1000000)`. Compute `D=floor(N*Q/L)`, `P1=P+D`, then token0 output `floor(floor(L*Q*D/P1)/P)`.

| Intermediate | Independently calculated integer |
|---|---:|
| Q | 79228162514264337593543950336 |
| N | 999500000000000000 |
| D | 135510640917570683864397866851 |
| P1 | 1367698790157946970110541127921282 |
| Rounded input `ceil(L*D/Q)` | 999500000000000000 |
| Fee `I-ceil(L*D/Q)` | 500000000000000 |
| Output | 3354316759 |

The loaded bitmap's next initialized tick is **195140**, at sqrtPriceX96 **1367977603659125784409971745193048**, strictly above P1. Thus this trade needs no liquidity transition. The output and P1 match both the adapter and the cached QuoterV2 bytes. Fresh-node equality remains unverified.

Runnable arithmetic check, using no adapter math:

```bash
python3 -B - <<'PY'
Q = 1 << 96
P = 1367563279517029399426676730054431
L = 584371440477331545
I = 10**18
N = I * 999500 // 1000000
D = N * Q // L
P1 = P + D
out = (L * Q * D // P1) // P
assert P1 == 1367698790157946970110541127921282
assert out == 3354316759
assert (L * D + Q - 1) // Q == N
assert I - N == 500000000000000
print(out, P1)
PY
```

## Math fidelity and test independence

Reference read directly: `<external-repos>/uniswap-v3-core/`, whose `package.json` identifies `@uniswap/v3-core` version `1.0.0`. I compared FullMath, TickMath, SqrtPriceMath, SwapMath, TickBitmap, LiquidityMath and the Pool.swap loop, including SafeCast/LowGasSafeMath/UnsafeMath semantics where relevant. No Solidity execution or new reference checkout was performed.

| Component | Result of comparison/check |
|---|---|
| FullMath | Python's full-width product/division plus quotient bounds is mathematically equivalent on uint256 operands. It need not reproduce the modular inverse or `-denominator & denominator` algorithm literally. Rounding-up overflow is checked. |
| TickMath | Magic constants, reciprocal, low-32-bit rounding-up, logarithm iterations and endpoint bounds agree. The extra final normalization of `r` in Python has no later consumer. Boundary tests and saved event prices pass. |
| SqrtPriceMath | Delta rounding flags, amount0 product/sum wrap checks, amount1 rounding and exact-output direction agree on inspected ordinary paths. The checked-add omission in finding 5 is a real helper divergence. |
| SwapMath | Exact-input fee discount, exact-output branch, output cap and partial-step residual-as-fee agree. The current cap is correct but unprotected by the existing suite, as the mutation below demonstrates. |
| TickBitmap | Inclusive downward and exclusive upward searches, negative floor compression and word endpoints agree. A separate brute-force set-bit search passed **120** cases over spacings 1/10/60/200, negative/positive boundary ticks and both directions. State's missing-word wrapper is the exception in finding 1. |
| LiquidityMath | Arithmetic plus bounds matches checked uint128/int128 addition on valid typed inputs. |
| Pool.swap loop | Negating liquidityNet downward, updating tick to tickNext−1 downward / tickNext upward, and per-step fees agree. Fee-growth/oracle/protocol-accounting writes and token transfer/callback execution are omitted; this is a quote state, not a full pool execution emulator. |

Intentional API differences: zero exact input returns `(0,self)` rather than Pool.swap's `AS` revert; an unfinished trade at the global price limit becomes `Unsupported` rather than a partial fill. Exact-output is not publicly exposed. Python helpers do not uniformly enforce Solidity argument widths; the fidelity assessment above concerns valid typed inputs. Missing negative signed-delta SafeCast calls cannot overflow int256 within the helpers' valid uint160/int128 inputs. No other numerical divergence was established in the reviewed quote paths.

**Observed baseline command:**

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_uniswap_v3_math.py tests/test_uniswap_v3_state.py
35 passed in 1.45s
```

Direct venv execution avoided uv synchronization/cache changes under the single-file restriction. No full-project test verdict is claimed.

**Fixture independence:** `scripts/uniswap_v3_online_check.py:136–160,182–188,249–252` obtains `quoter_out` from decoded raw QuoterV2 data, not `local_out`. I re-decoded the raw return words and reconciled all **18** fixture expected output/price pairs with them. `test_uniswap_v3_state.py:189–202` independently compares nine settled rows from the copied funded results. Both test files therefore contain independent expected values: upstream constants/events for the math file, and standing prices/QuoterV2/funded results for the state file. Their entire suites are not circular.

**Weak checks:** `test_quote_exact_in_matches_swap` compares a wrapper with the method it calls. The delta-rounding checks and fee test at `test_uniswap_v3_math.py:250–259` reuse implementation helpers. `test_recorded_replay_summary_matches` only inspects saved constants; despite its docstring, it does not compare a newly computed summary. These are consistency checks, not independent arithmetic proofs.

**Mutation actually tried:** remove the exact-output clamp condition at `math.py:404` in the imported module's memory, then rerun both existing test files. No file was changed:

```python
source = pathlib.Path(m.__file__).read_text()
source = source.replace(
    'if (not exact_in) and amount_out > (-amount_remaining):',
    'if False: # reviewer mutation: remove exact-output clamp',
)
exec(compile(source, m.__file__, 'exec'), m.__dict__)
pytest.main(['-q', '-p', 'no:cacheprovider',
             'tests/test_uniswap_v3_math.py', 'tests/test_uniswap_v3_state.py'])
```

Result: **35 passed in 1.09s**, despite an independently sourced upstream `SwapMath.spec.ts` vector now returning too much output:

```text
compute_swap_step(
  417332158212080721273783715441582,
  1452870262520218020823638996,
  159344665391607089467575320103, -1, 1)

original: (417332158212080721273783715441581, 1, 1, 1)
mutated:  (417332158212080721273783715441581, 1, 2, 1)
```

The mutation died with that process. I also ran four upstream SwapMath vectors for target-capped exact input/output and fully consumed exact input/output; all matched their independently fixed amount/fee expectations. These checks strengthen the present implementation verdict while showing a concrete regression gap.

## CSV recomputation: what the labels actually establish

I independently used integer rational formulas, without calling adapter math to produce the candidates. For each eligible pair, derive direction from the positive pool amount. Set `I` to positive input and `O` to the negated output; use previous price/liquidity `P,L` and observed terminal price `S`.

With `ceil(a/b)=(a+b-1)//b`, exact-input candidate price is `ceil(L*Q*P/(L*Q+N*P))` downward and `P+floor(N*Q/L)` upward, where `N=floor(I*999500/1000000)`. Exact-output candidate price is `P-ceil(O*Q/L)` downward and `ceil(L*Q*P/(L*Q-O*P))` upward. The third candidate supplies `S` itself. Derive input/output deltas at each candidate price, round input up/output down, and charge the appropriate residual or rounded-up fee. Compare all three fields `(terminal price, gross input, output)`.

| CSV | Rows | Eligible pairs | First-match exact_in | First-match exact_out | First-match limit |
|---|---:|---:|---:|---:|---:|
| crash1 | 3133 | 1061 | 809 | 229 | 23 |
| crash2 | 5010 | 1935 | 1496 | 411 | 28 |
| crash3 | 3337 | 1207 | 917 | 259 | 31 |
| Total | 11480 | 4203 | 3222 | 899 | 82 |

Zero unexplained pairs under this candidate-fitting procedure. Independent match sets: **3219 {exact_in,limit}; 899 {exact_out,limit}; 82 {limit}; 3 {exact_in}**. These sum to 4203 and give the task's 981 exact-input mismatches.

Reviewer-selected examples, all from crash1; amounts are raw token units and log indices identify the previous/current pair within the named block:

| Block; log indices | Input / observed output | Script label | Candidates fitting all fields |
|---|---|---|---|
| 23548435; 130→158 | 5848375000000000000 WETH / 24086084495 USDC | exact_in | exact_in, limit |
| 23548438; 479→545 | 702283133 USDC / 170418465265757682 WETH | exact_in | exact_in, limit |
| 23548460; 92→97 | 3048682033516753492 WETH / 12500000000 USDC | exact_out | exact_out, limit |
| 23548460; 166→171 | 15362220765 USDC / 3738403107931191944 WETH | exact_out | exact_out, limit |
| 23548491; 11→28 | 16392567046 USDC / 3986205301124413822 WETH | limit | limit |
| 23548597; 526→784 | 2868698789089678588 WETH / 11824258519 USDC | limit | limit |

For 23548460/92→97, exact-input predicts price **1237081900251624452773213989294208**, whereas observed and exact-output/limit price is **1237081900251624452773204854535643**. For 23548491/11→28, exact-input predicts output **3986205301186486764**, exceeding observed **3986205301124413822**, and exact-output predicts price **1235691332695232126100082725850053**, differing from observed **1235691332695232126100081517527040**. Thus the deterministic first-match labels are reproducible, but the original caller modes are not forced by the available data.

## Discovery provenance and state semantics

I decoded all **52** saved PoolCreated logs and reconciled every pool's creation block, fee and spacing with its inventory record. Baseline creation is **12376729**, transaction **0x125e0b641d4a4b08806bf52c0c6757648c9963bcda8681e4f996f09e00d4c2cc**, log index **101**. It is not a guessed creation block.

The **56** pair coverage entries exactly match the filters generated for eight tokens: **28 unordered pairs × two orderings**. All recorded reverse-order counts are zero; per-filter positive counts reconcile with the saved logs. The 57th coverage entry scans FeeAmountEnabled. Every entry spans **12369621–25896003** and contains `to_block_hash` equal to the standing-prices hash:

```text
0xf2c9645adf0b9f757587541f7552b6b18cf79d7817bc46bdf070f0bce3dba5a5
```

Fee topic3 is omitted, so the discovery filter does not assume the familiar tiers. Saved FeeAmountEnabled logs enumerate `(fee,spacing,block)` as **(500,10,12369621), (3000,60,12369621), (10000,200,12369621), (100,1,13604706)**. Inventory notes correctly restrict negative coverage to the scanned pairs. The prose “ever enabled” should be read only through the pinned upper block. The aggregated raw-log file does not retain each empty request/response envelope, so I verified internal consistency of the recorded scan, not independently that every past empty query was sent.

`capacity_ids()` is exactly `(record.pool_id,)`. Pricing maps are copied and wrapped in `MappingProxyType`; I cleared the original constructor dictionaries in memory and the state retained its 17 words and 1397 liquidityNet entries. Assigning to liquidityNet raises `TypeError`. Existing tests confirm scalar immutability and shared-depth depletion. `PoolRecord.config` and `discovered_by` remain shared mutable mappings, but the swap path reads frozen pricing fields rather than those metadata mappings.

Normal out-of-window trades and missing crossed liquidityNet data raise `Unsupported` before returning a result. Removing tick 195140's liquidityNet in memory and selling 10 WETH raises the explicit missing-initialized-tick error. This does not excuse the in-range bitmap hole in finding 1.

The constant **120000** gas value is explicitly called a flat estimate/label in state source and both adapter reports. The evaluator carries it as `gas_estimate`; it does not attach an additional “constant model” provenance field. I found no current rendering that calls this constant measured settlement gas. Future reports must preserve the distinction from the separately recorded QuoterV2 gas fields.

## Open risks and next-phase caveats

- **Fresh verification remains blocked.** Repeat the four cast reads when DNS/network access is available and check the block hash. Cached-byte agreement is not fresh-node verification. No successful new Multicall-versus-individual parity test was possible here.
- **Coverage for 10k WETH is state-dependent.** My cached local recomputation returns **22106231769877 raw USDC** at 23549991 with minimum tested symmetric word radius **2**, and **21854532328183** at 25896003 with minimum radius **1**. These 10k quotes have no newly obtained independent QuoterV2 check. Radius 8 covers these two cases, not every pool/block/direction. Expand on explicit missing-state failures, retain unsupported sizes, and distinguish missing evidence from physical liquidity exhaustion.
- **Multi-hop and splits need independent stateful validation.** Thread each reused pool's updated state through subsequent legs, conserve integer balances, and validate a real combined reference path. The two sequential 500-WETH unit checks are useful but are not an independent multi-hop settlement comparison. Single-pool cached success does not prove arbitrary execution orderings.
- **wstETH/stETH and transfer semantics remain unverified here.** The inventory identifies separate wstETH and stETH addresses. Do not transfer a rebasing-token assumption from one to the other, or treat unwrapping as immediate ETH redemption. Validate each route's actual pool tokens, conversions, historical transfer/balance behavior and usable output inventory before allowing it into the supported routing universe.
- **Only two of five pins have adapter tick-state fixtures.** The remaining three have standing prices and funded rows but were not locally reconstructed here. All 18 cached quote checks concern one pool; the other 51 need appropriately scoped support evidence.
- **Tests need targeted failure and arithmetic vectors.** The demonstrated exact-output-cap mutation survives the entire adapter suite. Include that upstream vector, the checked-add boundary, missing in-range bitmap words and unsuccessful dependent subcalls before relying on future refactors.
- **Canonical source identity:** this review used the locally available v3-core 1.0.0 sources, not a freshly downloaded release or a new on-chain bytecode comparison. For reproducibility, SHA-256 values of the seven compared files are recorded below.

```text
FullMath.sol       54087aee268a6938a85a408d7b14481b5c2c956c21508d5583f1bf48ec6d69ba
TickMath.sol       83cf64b2ca84001effd16e007b49bac5359143b6c3132bfe42907b2426a0c5f5
SqrtPriceMath.sol  ddd62e3a94346248677f30f1ab009ef015e71e4b8696dcca890eeabc9dc6c149
SwapMath.sol       d6cb9a153be4ea9fb2377ef88641ef7979b5cee6933162f1b732d0289e26e1b6
TickBitmap.sol     bd7a17c5134f0718eb7d856ddfc58d8347d32a8f661bed53aa3ad17c9aea09ba
LiquidityMath.sol  84d20a16d5346f6ec4c12dff4df23dda5d46e52d33f18aaaaac2e9e36ce4a072
UniswapV3Pool.sol  d515775b7f3ffe921dd70aca86b8bad16280fa4c122425d82b4dbea4dc564a7a
```
