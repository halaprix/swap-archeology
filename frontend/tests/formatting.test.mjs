import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-loader.mjs", import.meta.url);

const { calculateBaselineGain, formatRawUnits, parseDecimalToRaw } =
  await import("../src/lib/formatting.ts");

test("formatRawUnits: formats 18 decimal WETH without float precision loss", () => {
  assert.equal(formatRawUnits("100000000000000000000", 18), "100");
  assert.equal(formatRawUnits("100500000000000000000", 18), "100.5");
  assert.equal(formatRawUnits("50000000000000000", 18), "0.05");
});

test("formatRawUnits: formats 6 decimal USDC with thousand separators", () => {
  assert.equal(formatRawUnits("333396738584", 6, 4), "333,396.7385");
  assert.equal(formatRawUnits("333396738584", 6, 2), "333,396.73");
});

test("formatRawUnits: handles zero and edge cases", () => {
  assert.equal(formatRawUnits("0", 18), "0");
  assert.equal(formatRawUnits("", 18), "0");
  assert.equal(formatRawUnits(null, 18), "0");
  assert.equal(formatRawUnits(undefined, 18), "0");
});

test("parseDecimalToRaw: converts human decimals to exact raw integer string", () => {
  assert.equal(parseDecimalToRaw("100", 18), "100000000000000000000");
  assert.equal(parseDecimalToRaw("100.5", 18), "100500000000000000000");
  assert.equal(parseDecimalToRaw("1000", 6), "1000000000");
  assert.equal(parseDecimalToRaw("0.000001", 6), "1");
  assert.throws(() => parseDecimalToRaw("1.1234567", 6), /more than 6 decimal places/);
  assert.throws(() => parseDecimalToRaw("abc", 18), /valid positive decimal/);
});

test("calculateBaselineGain: accurately computes gain across historical report values", () => {
  const gain = calculateBaselineGain("332132193469", "333396738584", 6);
  assert.ok(gain);
  assert.equal(gain.isGain, true);
  assert.equal(gain.percentGain, "+0.38%");
  assert.equal(gain.formattedDelta, "+1,264.5451");
  assert.equal(gain.absoluteDeltaRaw, "1264545115");
});
