import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-loader.mjs", import.meta.url);
const {
  DEFAULT_FIVE_CRASHES,
  parseFiveCrashIndex,
  normalizeCrashStatus,
  statusBadgeVariant,
  statusDescription,
  familyBest,
  getFamilyLabel,
} = await import("../src/lib/liquidityGaps.ts");

test("DEFAULT_FIVE_CRASHES: defines 5 canonical Binance crash episodes with expected contract paths", () => {
  assert.equal(DEFAULT_FIVE_CRASHES.length, 5);

  const ids = DEFAULT_FIVE_CRASHES.map((c) => c.id);
  assert.deepEqual(ids, ["crash-1", "crash-2", "crash-3", "crash-4", "crash-5"]);

  const expectedDates = ["2025-02-03", "2025-02-25", "2025-04-07", "2025-06-21", "2025-10-10"];
  const expectedWindows = [
    { start: "2025-02-03T01:26:59Z", end: "2025-02-03T02:26:59Z" },
    { start: "2025-02-25T06:55:59Z", end: "2025-02-25T07:55:59Z" },
    { start: "2025-04-07T06:24:59Z", end: "2025-04-07T07:24:59Z" },
    { start: "2025-06-21T21:01:59Z", end: "2025-06-21T22:01:59Z" },
    { start: "2025-10-10T21:14:00Z", end: "2025-10-10T22:14:00Z" },
  ];
  for (let i = 0; i < 5; i++) {
    const crash = DEFAULT_FIVE_CRASHES[i];
    assert.match(crash.crashUtc, new RegExp(`^${expectedDates[i]}`));
    assert.equal(crash.status, "pending");
    assert.equal(crash.startUtc, expectedWindows[i].start);
    assert.equal(crash.endUtc, expectedWindows[i].end);
    assert.equal(crash.sourcesUrl, `/five-crash-liquidity/${crash.id}/sources.json`);
    assert.equal(crash.oracleReferencesUrl, `/five-crash-liquidity/${crash.id}/oracle-references.json`);
  }
});

test("parseFiveCrashIndex: validates schemaVersion 1 backend contract shape", () => {
  const valid = {
    schemaVersion: 1,
    crashes: [
      {
        id: "crash-1",
        label: "Crash 1 Feb 3",
        crashUtc: "2025-02-03T01:56:59Z",
        startUtc: "2025-02-03T01:00:00Z",
        endUtc: "2025-02-03T02:00:00Z",
        status: "collected",
        sourcesUrl: "/five-crash-liquidity/crash-1/sources.json",
        oracleReferencesUrl: "/five-crash-liquidity/crash-1/oracle-references.json",
        coverage: { blockCount: 302, stride: 1 },
      },
    ],
  };

  const parsed = parseFiveCrashIndex(valid);
  assert.notEqual(parsed, null);
  assert.equal(parsed?.schemaVersion, 1);
  assert.equal(parsed?.crashes.length, 1);
  assert.equal(parsed?.crashes[0].id, "crash-1");
  assert.equal(parsed?.crashes[0].status, "collected");
  assert.equal(parsed?.crashes[0].coverage?.blockCount, 302);
});

test("parseFiveCrashIndex: rejects malformed or unversioned index contracts", () => {
  assert.equal(parseFiveCrashIndex(null), null);
  assert.equal(parseFiveCrashIndex({}), null);
  assert.equal(parseFiveCrashIndex({ schemaVersion: 2, crashes: [] }), null);
  assert.equal(parseFiveCrashIndex({ schemaVersion: 1, crashes: "not-an-array" }), null);
  // Missing required crash fields
  assert.equal(parseFiveCrashIndex({ schemaVersion: 1, crashes: [{ id: "c1" }] }), null);
  assert.equal(parseFiveCrashIndex({ schemaVersion: 1, crashes: [{ sourcesUrl: "/path" }] }), null);
});

test("normalizeCrashStatus: correctly normalizes statuses and defaults unknown to pending", () => {
  assert.equal(normalizeCrashStatus("collected"), "collected");
  assert.equal(normalizeCrashStatus("Completed"), "collected");
  assert.equal(normalizeCrashStatus("partial"), "partial");
  assert.equal(normalizeCrashStatus("failed"), "failed");
  assert.equal(normalizeCrashStatus("ERROR"), "failed");
  assert.equal(normalizeCrashStatus("pending"), "pending");
  assert.equal(normalizeCrashStatus("unknown_status"), "pending");
  assert.equal(normalizeCrashStatus(null), "pending");
  assert.equal(normalizeCrashStatus(undefined), "pending");
});

test("statusBadgeVariant and statusDescription provide consistent semantics", () => {
  assert.equal(statusBadgeVariant("collected"), "success");
  assert.equal(statusBadgeVariant("partial"), "warning");
  assert.equal(statusBadgeVariant("failed"), "error");
  assert.equal(statusBadgeVariant("pending"), "neutral");

  assert.match(statusDescription("collected"), /collected/i);
  assert.match(statusDescription("partial"), /partial/i);
  assert.match(statusDescription("failed"), /failed/i);
  assert.match(statusDescription("pending"), /pending/i);
});

test("familyBest: selects the top direct pool price across rows without zero interpolation", () => {
  const pools = [
    { id: "pool_low", family: "uniswap_v3", address: "0x1", inputSymbol: "WETH", feeBps: 5, label: "UniV3 5bps" },
    { id: "pool_high", family: "uniswap_v3", address: "0x2", inputSymbol: "WETH", feeBps: 30, label: "UniV3 30bps" },
  ];

  const rows = [
    {
      block: 100,
      blockHash: "0x100",
      timestamp: "2025-02-03T01:00:00Z",
      chainlink: 2500,
      aave: 2500,
      aggregates: { ETH: { 1: null, 10: null, 100: null }, WETH: { 1: null, 10: null, 100: null } },
      pools: {
        pool_low: { 100: { price: 2490, amountOut: "2490000000" } },
        pool_high: { 100: { price: 2510, amountOut: "2510000000" } },
      },
      availability: {},
    },
    {
      block: 101,
      blockHash: "0x101",
      timestamp: "2025-02-03T01:00:12Z",
      chainlink: 2505,
      aave: 2505,
      aggregates: { ETH: { 1: null, 10: null, 100: null }, WETH: { 1: null, 10: null, 100: null } },
      pools: {
        pool_low: { 100: { price: null, amountOut: null, reason: "exhausted" } },
        pool_high: { 100: { price: null, amountOut: null, reason: "reverted" } },
      },
      availability: {},
    },
  ];

  const series = familyBest(rows, pools, "uniswap_v3", "100");
  assert.equal(series.length, 2);
  assert.equal(series[0], 2510);
  assert.equal(series[1], null); // Missing direct quote must NEVER be plotted as 0
});

test("getFamilyLabel: prioritizes explicit label from families catalog then falls back to venue metadata", () => {
  const families = [{ id: "uniswap_v3", label: "Uniswap v3 (Direct)" }];
  assert.equal(getFamilyLabel("uniswap_v3", families), "Uniswap v3 (Direct)");
  assert.equal(getFamilyLabel("uniswap_v3", []), "Uniswap v3");
  assert.equal(getFamilyLabel("unknown_protocol", []), "Unknown Protocol");
});
