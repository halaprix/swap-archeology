import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-loader.mjs", import.meta.url);

const [{ buildRouteFlowGraph }, { buildRouteFlowLayout }, { resolveTokenMetadata, formatTokenAmount }, formatting, catalog, venues] =
  await Promise.all([
    import("../src/lib/routeFlowGraph.ts"),
    import("../src/lib/routeFlowLayout.ts"),
    import("../src/lib/tokenMetadata.ts"),
    import("../src/lib/formatting.ts"),
    import("../src/lib/catalog.ts"),
    import("../src/lib/venues.ts"),
  ]);
const { calculateBaselineGain, shortenHash } = formatting;
const { filterReports, getAmountFilterKey, normalizeSourceSet, getSavedReports, getTokens } = catalog;
const { getVenueInfo, ALL_SOURCE_FAMILIES } = venues;

const TOKENS = {
  WETH: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
  USDC: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
  USDT: "0xdac17f958d2ee523a2206206994597c13d831ec7",
  WBTC: "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
  USDS: "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
  UNKNOWN: "0x1111111111111111111111111111111111111111",
};

const req = {
  amount_in: "100",
  token_in: TOKENS.WETH,
  symbol_in: "WETH",
  token_out: TOKENS.USDC,
  symbol_out: "USDC",
  decimals_in: "18",
  decimals_out: "6",
};

test("buildRouteFlowGraph: 3-step split truthful topology", () => {
  const steps = [
    { pool_id: "uniswap_v3:pool1", token_in: TOKENS.WETH, token_out: TOKENS.USDC, amount_in: "25", amount_out: "85000" },
    { pool_id: "curve:pool2", token_in: TOKENS.WETH, token_out: TOKENS.USDT, amount_in: "75", amount_out: "255000" },
    { pool_id: "uniswap_v2:pool3", token_in: TOKENS.USDT, token_out: TOKENS.USDC, amount_in: "255000", amount_out: "255000" },
  ];
  const graph = buildRouteFlowGraph(req, steps);
  assert.deepEqual(graph.nodes.map((node) => node.id), ["input", "step-0", "step-1", "step-2", "output"]);
  const inputEdges = graph.edges.filter((edge) => edge.isInputEdge);
  assert.deepEqual(inputEdges.map((edge) => [edge.to, edge.amountRaw]), [["step-0", "25"], ["step-1", "75"]]);
  assert.equal(inputEdges.some((edge) => edge.to === "step-2"), false);
  const intermediateEdges = graph.edges.filter((edge) => !edge.isInputEdge && !edge.isOutputEdge);
  assert.deepEqual(intermediateEdges.map((edge) => [edge.from, edge.to, edge.tokenAddress, edge.amountRaw]), [["step-1", "step-2", TOKENS.USDT, "255000"]]);
  const outputEdges = graph.edges.filter((edge) => edge.isOutputEdge);
  assert.deepEqual(outputEdges.map((edge) => [edge.from, edge.amountRaw]), [["step-0", "85000"], ["step-2", "255000"]]);
  assert.equal(outputEdges.some((edge) => edge.from === "step-1"), false);
  assert.equal(graph.shortfalls.length, 0);
  assert.equal(graph.leftovers.length, 0);
});

test("buildRouteFlowLayout: dependent USDT to USDC operation moves into the next column", () => {
  const graph = buildRouteFlowGraph(req, [
    { pool_id: "v3:direct", token_in: TOKENS.WETH, token_out: TOKENS.USDC, amount_in: "25", amount_out: "85000" },
    { pool_id: "curve:middle", token_in: TOKENS.WETH, token_out: TOKENS.USDT, amount_in: "75", amount_out: "255000" },
    { pool_id: "v2:final", token_in: TOKENS.USDT, token_out: TOKENS.USDC, amount_in: "255000", amount_out: "255000" },
  ]);
  assert.deepEqual(buildRouteFlowLayout(graph, 3).stepColumns, [1, 1, 2]);
});

test("buildRouteFlowGraph: merge case from multiple steps", () => {
  const graph = buildRouteFlowGraph(req, [
    { pool_id: "p1", token_in: TOKENS.WETH, token_out: TOKENS.USDT, amount_in: "40", amount_out: "100" },
    { pool_id: "p2", token_in: TOKENS.WETH, token_out: TOKENS.USDT, amount_in: "60", amount_out: "200" },
    { pool_id: "p3", token_in: TOKENS.USDT, token_out: TOKENS.USDC, amount_in: "300", amount_out: "299" },
  ]);
  assert.deepEqual(graph.edges.filter((edge) => !edge.isInputEdge && !edge.isOutputEdge).map((edge) => [edge.from, edge.amountRaw]), [["step-0", "100"], ["step-1", "200"]]);
  assert.deepEqual(graph.edges.filter((edge) => edge.isOutputEdge).map((edge) => edge.amountRaw), ["299"]);
  assert.equal(graph.shortfalls.length, 0);
  assert.equal(graph.leftovers.length, 0);
});

test("buildRouteFlowGraph: identifies shortfalls without inventing synthetic edges", () => {
  const graph = buildRouteFlowGraph(req, [{ pool_id: "p", token_in: TOKENS.USDT, token_out: TOKENS.USDC, amount_in: "100", amount_out: "99" }]);
  assert.equal(graph.edges.length, 1);
  assert.deepEqual(graph.shortfalls, [{ stepIndex: 0, tokenAddress: TOKENS.USDT, missingAmountRaw: "100" }]);
  assert.deepEqual(graph.leftovers.map((leftover) => leftover.amountRaw), ["100"]);
});

test("buildRouteFlowGraph: preserves leftovers without routing to output", () => {
  const graph = buildRouteFlowGraph(req, [{ pool_id: "p", token_in: TOKENS.WETH, token_out: TOKENS.USDT, amount_in: "40", amount_out: "100" }]);
  assert.equal(graph.edges.filter((edge) => edge.isOutputEdge).length, 0);
  assert.deepEqual(graph.leftovers.map((leftover) => [leftover.tokenAddress, leftover.amountRaw]), [[TOKENS.WETH, "60"], [TOKENS.USDT, "100"]]);
});

test("resolveTokenMetadata: exact address match, 6-decimal intermediary, and unknown token", () => {
  const tokens = [{ symbol: "USDC", address: TOKENS.USDC, decimals: 6 }];
  assert.deepEqual(resolveTokenMetadata(TOKENS.USDC, tokens), { symbol: "USDC", address: TOKENS.USDC, decimals: 6, isKnown: true });
  assert.equal(resolveTokenMetadata(TOKENS.UNKNOWN, tokens).decimals, null);
  assert.equal(resolveTokenMetadata(TOKENS.UNKNOWN, tokens).symbol, shortenHash(TOKENS.UNKNOWN, 6, 4));
  assert.equal(formatTokenAmount("1234567", resolveTokenMetadata(TOKENS.UNKNOWN, tokens)), "1,234,567 (raw)");
});

test("filterReports and getAmountFilterKey: strictly excludes 1 WETH and 1000 WETH when 100 WETH is selected", () => {
  const reports = [1, 100, 1000].map((amount) => ({ report: { block: "1", request: { symbol_in: "WETH", symbol_out: "USDC", amount_in: String(amount), token_in: TOKENS.WETH }, requested_solver: "baseline" } }));
  const result = filterReports(reports, { amountKey: getAmountFilterKey("100", TOKENS.WETH) });
  assert.equal(result.length, 1);
  assert.equal(result[0].report.request.amount_in, "100");
});

test("normalizeSourceSet: safely handles object, array, and null without crashing", () => {
  assert.equal(normalizeSourceSet({ source_subset: { sources: ["curve", "uniswap_v3"] } }), "curve,uniswap_v3");
  assert.equal(normalizeSourceSet({ source_subset: ["uniswap_v3", "curve"] }), "curve,uniswap_v3");
  assert.equal(normalizeSourceSet({ source_subset: null, selected_families: ["curve"] }), "curve");
});

test("calculateBaselineGain: handles zero delta, positive delta, and negative delta", () => {
  assert.equal(calculateBaselineGain("100", "100", 0).percentGain, "0.00%");
  assert.equal(calculateBaselineGain("100", "101", 0).formattedDelta, "+1");
  assert.equal(calculateBaselineGain("100", "99", 0).formattedDelta, "-1");
});

test("catalog reports: every feasible final output equals best_split", () => {
  const feasible = getSavedReports().filter((entry) => entry.report.best_split !== null);
  assert.ok(feasible.length > 0);
  for (const entry of feasible) {
    const report = entry.report;
    const graph = buildRouteFlowGraph(report.request, report.best_split.steps);
    assert.equal(graph.shortfalls.length, 0, entry.id);
    const output = graph.edges
      .filter((edge) => edge.isOutputEdge)
      .reduce((sum, edge) => sum + edge.amount, 0n);
    assert.equal(output.toString(), report.best_split.amount_out, entry.id);
  }
});

test("resolveTokenMetadata: WBTC 8dec and USDS 18dec intermediaries resolve accurately", () => {
  const wbtc = resolveTokenMetadata(TOKENS.WBTC);
  assert.equal(wbtc.symbol, "WBTC");
  assert.equal(wbtc.decimals, 8);
  assert.equal(wbtc.isKnown, true);
  assert.equal(formatTokenAmount("2336098", wbtc, 4), "0.0233");
  assert.equal(formatTokenAmount("2336098", wbtc, 6), "0.02336");
  assert.equal(formatTokenAmount("100000000", wbtc, 4), "1");

  const usds = resolveTokenMetadata(TOKENS.USDS);
  assert.equal(usds.symbol, "USDS");
  assert.equal(usds.decimals, 18);
  assert.equal(usds.isKnown, true);
  assert.equal(formatTokenAmount("100000000000000000000000", usds, 4), "100,000");
});

test("venues: PancakeSwap v3 is supported in KNOWN_FAMILIES with distinct tokens", () => {
  assert.ok(ALL_SOURCE_FAMILIES.includes("pancake_v3"));
  const info = getVenueInfo("pancake_v3");
  assert.equal(info.family, "pancake_v3");
  assert.equal(info.name, "PancakeSwap v3");
  assert.equal(info.colorVar, "var(--venue-pancake-v3)");
  assert.equal(info.badgeBg, "var(--venue-pancake-v3-bg)");

  const customInfo = getVenueInfo("custom_dex_v1");
  assert.equal(customInfo.name, "Custom Dex V1");
});

test("buildRouteFlowGraph & layout: handles multi-hop route with WBTC intermediary step", () => {
  const wbtcRouteSteps = [
    { pool_id: "pancake_v3:pool1", token_in: TOKENS.WETH, token_out: TOKENS.WBTC, amount_in: "700000000000000000", amount_out: "2336098" },
    { pool_id: "uniswap_v3:pool2", token_in: TOKENS.WBTC, token_out: TOKENS.USDC, amount_in: "2336098", amount_out: "2650000000" },
    { pool_id: "uniswap_v3:pool3", token_in: TOKENS.WETH, token_out: TOKENS.WBTC, amount_in: "300000000000000000", amount_out: "1003873" },
    { pool_id: "uniswap_v3:pool4", token_in: TOKENS.WBTC, token_out: TOKENS.USDC, amount_in: "1003873", amount_out: "1137418875" },
  ];
  const wbtcReq = {
    amount_in: "1000000000000000000",
    token_in: TOKENS.WETH,
    symbol_in: "WETH",
    token_out: TOKENS.USDC,
    symbol_out: "USDC",
    decimals_in: "18",
    decimals_out: "6",
  };
  const graph = buildRouteFlowGraph(wbtcReq, wbtcRouteSteps);
  assert.equal(graph.shortfalls.length, 0);
  assert.equal(graph.leftovers.length, 0);

  // Layout should place WETH->WBTC in column 1, and dependent WBTC->USDC in column 2
  const layout = buildRouteFlowLayout(graph, 4);
  assert.deepEqual(layout.stepColumns, [1, 2, 1, 2]);
  assert.equal(layout.maxStepColumn, 2);

  // Edges between Step 0 and Step 1, Step 2 and Step 3 carry WBTC
  const interEdges = graph.edges.filter((e) => !e.isInputEdge && !e.isOutputEdge);
  assert.equal(interEdges.length, 2);
  assert.equal(interEdges[0].from, "step-0");
  assert.equal(interEdges[0].to, "step-1");
  assert.equal(interEdges[0].tokenAddress, TOKENS.WBTC);
  assert.equal(interEdges[0].amountRaw, "2336098");

  assert.equal(interEdges[1].from, "step-2");
  assert.equal(interEdges[1].to, "step-3");
  assert.equal(interEdges[1].tokenAddress, TOKENS.WBTC);
  assert.equal(interEdges[1].amountRaw, "1003873");
});

test("catalog getTokens: contains WBTC 8dec and USDS 18dec", () => {
  const tokens = getTokens();
  const wbtc = tokens.find((t) => t.address.toLowerCase() === TOKENS.WBTC.toLowerCase());
  const usds = tokens.find((t) => t.address.toLowerCase() === TOKENS.USDS.toLowerCase());
  assert.ok(wbtc);
  assert.equal(wbtc.decimals, 8);
  assert.equal(wbtc.symbol, "WBTC");
  assert.ok(usds);
  assert.equal(usds.decimals, 18);
  assert.equal(usds.symbol, "USDS");
});
