import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-loader.mjs", import.meta.url);

const { sanitizeErrorMessage, validateQuoteRequest } =
  await import("../src/lib/validation.ts");

test("validateQuoteRequest: accepts valid decimal block number request", () => {
  const result = validateQuoteRequest({
    block: "25896003",
    tokenIn: "USDC",
    tokenOut: "USDT",
    amount: "1000.5",
    solver: "baseline",
    sources: ["uniswap_v3", "curve"],
  });
  assert.deepEqual(result.data, {
    block: "25896003",
    tokenIn: "USDC",
    tokenOut: "USDT",
    amount: "1000.5",
    solver: "baseline",
    sources: ["uniswap_v3", "curve"],
  });
  assert.equal(result.error, undefined);
});

test("validateQuoteRequest: accepts valid 0x block hash", () => {
  const result = validateQuoteRequest({
    block: `0x${"ab".repeat(32)}`,
    tokenIn: "0xabc",
    tokenOut: "0xdef",
    amount: "1",
  });
  assert.equal(result.data?.block, `0x${"ab".repeat(32)}`);
  assert.equal(result.data?.solver, "baseline");
});

test("validateQuoteRequest: rejects invalid inputs", () => {
  assert.equal(validateQuoteRequest(null).error.code, "invalid_request");
  assert.equal(validateQuoteRequest({ block: "0", tokenIn: "A", tokenOut: "B", amount: "1" }).error.code, "invalid_block");
  assert.equal(validateQuoteRequest({ block: "1", tokenIn: "", tokenOut: "B", amount: "1" }).error.code, "invalid_token_in");
  assert.equal(validateQuoteRequest({ block: "1", tokenIn: "A", tokenOut: "B", amount: "0" }).error.code, "invalid_amount");
  assert.equal(validateQuoteRequest({ block: "1", tokenIn: "A", tokenOut: "B", amount: "1", solver: "dual" }).error.code, "invalid_solver");
  assert.equal(validateQuoteRequest({ block: "1", tokenIn: "A", tokenOut: "B", amount: "1", sources: [] }).error.code, "invalid_sources");
});

test("validateQuoteRequest: accepts high precision amounts within 78 integer and 36 fraction digits", () => {
  const result = validateQuoteRequest({
    block: 25896003,
    tokenIn: "USDC",
    tokenOut: "USDT",
    amount: `${"9".repeat(78)}.${"1".repeat(36)}`,
  });
  assert.equal(result.data?.amount, `${"9".repeat(78)}.${"1".repeat(36)}`);
});

test("sanitizeErrorMessage: redacts URLs and local paths", () => {
  const result = sanitizeErrorMessage("failed at /home/secret/file.py:12 https://internal.example/x\nretry");
  assert.equal(result, "failed at [internal path]:12 [internal bridge] retry");
  assert.equal(result.includes("secret"), false);
  assert.equal(result.includes("internal.example"), false);
});
