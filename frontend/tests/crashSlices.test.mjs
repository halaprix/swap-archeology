import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-loader.mjs", import.meta.url);
const { amountKey, chronological, pairKey, parseCrashSlices } = await import("../src/lib/crashSlices.ts");

test("crash-slices accepts only the collection-model-only v1 shape", () => {
  const data = { schemaVersion: 1, generatedAt: "2026-09-09T00:00:00Z", qualificationMode: "collection-model-only", rows: [], slices: [], feeds: [] };
  assert.deepEqual(parseCrashSlices(data), data);
  assert.equal(parseCrashSlices({ ...data, qualificationMode: "live" }), null);
  assert.equal(parseCrashSlices({ ...data, schemaVersion: 2 }), null);
});

test("crash-slices filters retain exact raw amount identity and chronological blocks", () => {
  const first = { timestampISO: "2026-01-01T00:01:00Z", block: 2, tokenIn: "WETH", tokenOut: "USDC", amountRaw: "100", amount: "0.0000000000000001" };
  const second = { ...first, timestampISO: "2026-01-01T00:00:00Z", block: 1, amountRaw: "1000" };
  assert.equal(pairKey(first), "WETH → USDC");
  assert.notEqual(amountKey(first), amountKey(second));
  assert.deepEqual(chronological([first, second]), [second, first]);
});
