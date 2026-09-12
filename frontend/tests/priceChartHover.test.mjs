import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import React from "react";
import { renderToString } from "react-dom/server";

// Transpile LiquidityGapExplorer.tsx with React JSX support for Node testing
const source = fs.readFileSync(new URL("../src/components/history/LiquidityGapExplorer.tsx", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.React,
    esModuleInterop: true,
  },
});

const customRequire = (id) => {
  if (id === "react") return React;
  if (id === "@/lib/octoberSources") return { linePath: () => "" };
  return {};
};

const moduleObj = { exports: {} };
const fn = new Function("require", "exports", "module", outputText);
fn(customRequire, moduleObj.exports, moduleObj);

const { formatChartObservation, chartPriceFmt, PriceChart } = moduleObj.exports;

test("formatChartObservation: formats positive prices with 4-decimal precision and USDC", () => {
  const whole = formatChartObservation(2500);
  assert.equal(whole.isAvailable, true);
  assert.equal(whole.formatted, "2,500.0000 USDC");
  assert.equal(chartPriceFmt.format(2500), "2,500.0000");

  const fractional = formatChartObservation(2415.82);
  assert.equal(fractional.isAvailable, true);
  assert.equal(fractional.formatted, "2,415.8200 USDC");

  const rounded = formatChartObservation(2415.82346);
  assert.equal(rounded.isAvailable, true);
  assert.equal(rounded.formatted, "2,415.8235 USDC");
});

test("formatChartObservation: missing observations are explicitly unavailable, never zero", () => {
  const nullObs = formatChartObservation(null);
  assert.equal(nullObs.isAvailable, false);
  assert.equal(nullObs.formatted, "unavailable");

  const undefObs = formatChartObservation(undefined);
  assert.equal(undefObs.isAvailable, false);
  assert.equal(undefObs.formatted, "unavailable");

  const nanObs = formatChartObservation(NaN);
  assert.equal(nanObs.isAvailable, false);
  assert.equal(nanObs.formatted, "unavailable");

  const zeroObs = formatChartObservation(0);
  assert.equal(zeroObs.isAvailable, false);
  assert.equal(zeroObs.formatted, "unavailable");
  assert.notEqual(zeroObs.formatted, "0");
  assert.notEqual(zeroObs.formatted, "0.0000 USDC");

  const negativeObs = formatChartObservation(-10);
  assert.equal(negativeObs.isAvailable, false);
  assert.equal(negativeObs.formatted, "unavailable");
});

test("formatChartObservation: preserves detailed unavailable status and reason", () => {
  const statusOnly = formatChartObservation(null, { status: "missing_round" });
  assert.equal(statusOnly.isAvailable, false);
  assert.equal(statusOnly.formatted, "unavailable (missing_round)");

  const statusWithReason = formatChartObservation(null, { status: "stale_feed", reason: "heartbeat_exceeded" });
  assert.equal(statusWithReason.isAvailable, false);
  assert.equal(statusWithReason.formatted, "unavailable (stale_feed: heartbeat_exceeded)");
});

test("PriceChart: renders colored dots for visible selected values and skips unavailable", () => {
  const rows = [
    { block: 25896001, blockHash: "0xabc1", timestamp: "2025-10-10T21:14:00Z" },
    { block: 25896002, blockHash: "0xabc2", timestamp: "2025-10-10T21:14:12Z" },
  ];
  const series = [
    { id: "uni_v3", label: "Uniswap v3", color: "#6b4ea2", values: [2500.5, 2510.0] },
    { id: "uni_v2", label: "Uniswap v2", color: "#147d8b", values: [null, 2505.0] },
  ];

  const html = renderToString(React.createElement(PriceChart, { rows, series, selected: 0, onSelect: () => {} }));

  // Dot for visible series at index 0 (#6b4ea2) should be present
  assert.equal(html.includes('fill="#6b4ea2"'), true);

  // Circle elements should only exist for the visible series:
  const circleMatches = html.match(/<circle[^>]+>/g) || [];
  assert.equal(circleMatches.length, 1);
  assert.equal(circleMatches[0].includes('fill="#6b4ea2"'), true);
});
