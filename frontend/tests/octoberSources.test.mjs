import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
register("./ts-loader.mjs", import.meta.url);
const { bestDirectPool, linePath, alignOracleSeries, alignOracleValue, KNOWN_ORACLE_SOURCES } = await import("../src/lib/octoberSources.ts");
test("October source helpers choose the best direct pool and break null observations", () => {
  const pools = [{ id: "v3", family: "uniswap_v3" }, { id: "v2", family: "uniswap_v2" }];
  assert.deepEqual(bestDirectPool(pools, { v3: { price: 2700 }, v2: { price: 2701 } }), { id: "v2", price: 2701 });
  assert.equal(linePath([1, null, 3], (value, index) => ({ x: index, y: Number(value), value })), "M0,1 M2,3 ");
});

test("alignOracleSeries: matches by exact block and hash, never by array index", () => {
  const baseRows = [
    { block: 100, blockHash: "0x100" },
    { block: 101, blockHash: "0x101" },
    { block: 102, blockHash: "0x102" },
  ];
  // Sidecar rows in reversed order with extra row
  const sidecar = {
    schemaVersion: 1,
    sources: [
      { id: "oneinch_spot", label: "1inch Spot", kind: "spot", description: "1inch" },
      { id: "uniswap_v3_twap_300", label: "UniV3 300s", kind: "twap", windowSeconds: 300, description: "300s" }
    ],
    rows: [
      { block: 102, blockHash: "0x102", timestamp: "t2", values: { oneinch_spot: { price: 3720, status: "available" } } },
      { block: 999, blockHash: "0x999", timestamp: "t0", values: { oneinch_spot: { price: 9999, status: "available" } } },
      { block: 100, blockHash: "0x100", timestamp: "t0", values: { oneinch_spot: { price: 3700, status: "available" } } },
      { block: 101, blockHash: "0x101", timestamp: "t1", values: { oneinch_spot: { price: 3710, status: "available" } } },
    ]
  };

  const aligned = alignOracleSeries(baseRows, sidecar, "oneinch_spot");
  assert.equal(aligned.length, 3);
  assert.equal(aligned[0].price, 3700);
  assert.equal(aligned[1].price, 3710);
  assert.equal(aligned[2].price, 3720);
});

test("alignOracleSeries: rejects block hash mismatch and records honest reason", () => {
  const baseRows = [
    { block: 100, blockHash: "0x100_canon" },
  ];
  const sidecar = {
    schemaVersion: 1,
    sources: [],
    rows: [
      { block: 100, blockHash: "0x100_reorg", values: { oneinch_spot: { price: 3700, status: "available" } } }
    ]
  };

  const aligned = alignOracleSeries(baseRows, sidecar, "oneinch_spot");
  assert.equal(aligned[0].price, null);
  assert.equal(aligned[0].status, "hash_mismatch");
  assert.match(aligned[0].reason, /hash mismatch/i);
});

test("alignOracleSeries: handles missing blocks and null/unloaded sidecar gracefully", () => {
  const baseRows = [
    { block: 100, blockHash: "0x100" },
    { block: 101, blockHash: "0x101" },
  ];

  // Null/unloaded sidecar
  const unloaded = alignOracleSeries(baseRows, null, "oneinch_spot");
  assert.equal(unloaded.length, 2);
  assert.equal(unloaded[0].price, null);
  assert.equal(unloaded[0].status, "unloaded");

  // Missing block in sidecar
  const sidecar = {
    schemaVersion: 1,
    sources: [],
    rows: [
      { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "available" } } }
    ]
  };
  const withMissing = alignOracleSeries(baseRows, sidecar, "oneinch_spot");
  assert.equal(withMissing[0].price, 3700);
  assert.equal(withMissing[1].price, null);
  assert.equal(withMissing[1].status, "missing_block");
});

test("alignOracleSeries: preserves null price and explicit status/reason from sidecar", () => {
  const baseRows = [{ block: 100, blockHash: "0x100" }];
  const sidecar = {
    schemaVersion: 1,
    sources: [],
    rows: [
      {
        block: 100,
        blockHash: "0x100",
        values: {
          uniswap_v3_twap_300: { price: null, status: "insufficient_history", reason: "Window extends prior to pool creation" }
        }
      }
    ]
  };
  const aligned = alignOracleSeries(baseRows, sidecar, "uniswap_v3_twap_300");
  assert.equal(aligned[0].price, null);
  assert.equal(aligned[0].status, "insufficient_history");
  assert.equal(aligned[0].reason, "Window extends prior to pool creation");
});

test("linePath produces honest breaks for null aligned values", () => {
  const aligned = [
    { price: 3700 },
    { price: null },
    { price: 3720 }
  ];
  const path = linePath(aligned, (row, index) => ({ x: index * 10, y: row.price ?? 0, value: row.price }));
  assert.equal(path, "M0,3700 M20,3720 ");
});

test("KNOWN_ORACLE_SOURCES: defines spot vs TWAP with window and distinct design colors", () => {
  assert.equal(KNOWN_ORACLE_SOURCES.oneinch_spot.kind, "spot");
  assert.equal(KNOWN_ORACLE_SOURCES.oneinch_spot.defaultVisible, true);
  assert.match(KNOWN_ORACLE_SOURCES.oneinch_spot.label, /spot/i);

  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_300.kind, "twap");
  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_300.windowSeconds, 300);
  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_300.defaultVisible, true);
  assert.match(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_300.label, /300s/);

  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_60.kind, "twap");
  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_60.windowSeconds, 60);
  assert.equal(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_60.defaultVisible, false);
  assert.match(KNOWN_ORACLE_SOURCES.uniswap_v3_twap_60.label, /60s/);

  // All colors are non-empty and distinct
  const colors = [
    KNOWN_ORACLE_SOURCES.oneinch_spot.color,
    KNOWN_ORACLE_SOURCES.uniswap_v3_twap_300.color,
    KNOWN_ORACLE_SOURCES.uniswap_v3_twap_60.color
  ];
  assert.equal(new Set(colors).size, 3);
});

test("alignOracleSeries: handles missing source key within existing row", () => {
  const baseRows = [{ block: 100, blockHash: "0x100" }];
  const sidecar = {
    schemaVersion: 1,
    sources: [],
    rows: [
      { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "available" } } }
    ]
  };
  const aligned = alignOracleSeries(baseRows, sidecar, "uniswap_v3_twap_60");
  assert.equal(aligned[0].price, null);
  assert.equal(aligned[0].status, "missing_source");
  assert.match(aligned[0].reason, /uniswap_v3_twap_60 missing/);
});

test("alignOracleSeries: partial reorg hash mismatch only invalidates the mismatched block", () => {
  const baseRows = [
    { block: 200, blockHash: "0x200_good" },
    { block: 201, blockHash: "0x201_good" },
    { block: 202, blockHash: "0x202_good" },
  ];
  const sidecar = {
    schemaVersion: 1,
    sources: [],
    rows: [
      { block: 200, blockHash: "0x200_good", values: { oneinch_spot: { price: 3700, status: "available" } } },
      { block: 201, blockHash: "0x201_reorg_bad", values: { oneinch_spot: { price: 3710, status: "available" } } },
      { block: 202, blockHash: "0x202_good", values: { oneinch_spot: { price: 3720, status: "available" } } },
    ]
  };
  const aligned = alignOracleSeries(baseRows, sidecar, "oneinch_spot");
  assert.equal(aligned[0].price, 3700);
  assert.equal(aligned[1].price, null);
  assert.equal(aligned[1].status, "hash_mismatch");
  assert.equal(aligned[2].price, 3720);
});

test("alignOracleValue: fail-closed on non-success status even when price is a number", () => {
  const baseRow = { block: 100, blockHash: "0x100" };

  // Reverted status with a numeric price must yield null price and retain status/reason
  const reverted = alignOracleValue(
    baseRow,
    { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "reverted", reason: "call reverted" } } },
    "oneinch_spot"
  );
  assert.equal(reverted.price, null);
  assert.equal(reverted.status, "reverted");
  assert.equal(reverted.reason, "call reverted");

  // Unavailable status with a numeric price must yield null price
  const unavailable = alignOracleValue(
    baseRow,
    { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "unavailable", reason: "stale data" } } },
    "oneinch_spot"
  );
  assert.equal(unavailable.price, null);
  assert.equal(unavailable.status, "unavailable");
  assert.equal(unavailable.reason, "stale data");

  // Collector status 'ok' yields price
  const ok = alignOracleValue(
    baseRow,
    { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3750, status: "ok" } } },
    "oneinch_spot"
  );
  assert.equal(ok.price, 3750);
  assert.equal(ok.status, "ok");

  // Legacy status 'available' yields price
  const available = alignOracleValue(
    baseRow,
    { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3760, status: "available" } } },
    "oneinch_spot"
  );
  assert.equal(available.price, 3760);
  assert.equal(available.status, "available");
});

test("alignOracleValue: explicitly checks baseRow.block === oracleRow.block in shared helper", () => {
  const baseRow = { block: 100, blockHash: "0x100" };
  const oracleRow = { block: 101, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "ok" } } };

  const result = alignOracleValue(baseRow, oracleRow, "oneinch_spot");
  assert.equal(result.price, null);
  assert.equal(result.status, "block_mismatch");
  assert.match(result.reason, /block number mismatch/i);
});

test("alignOracleValue: handles missing blockHash safely without calling .slice on undefined", () => {
  const baseRowMissingHash = { block: 100, blockHash: undefined };
  const oracleRow = { block: 100, blockHash: "0x100", values: { oneinch_spot: { price: 3700, status: "ok" } } };

  // Must not throw TypeError: Cannot read properties of undefined (reading 'slice')
  assert.doesNotThrow(() => {
    const result = alignOracleValue(baseRowMissingHash, oracleRow, "oneinch_spot");
    assert.equal(result.price, null);
    assert.equal(result.status, "hash_mismatch");
  });

  const baseRow = { block: 100, blockHash: "0x100" };
  const oracleRowMissingHash = { block: 100, blockHash: undefined, values: { oneinch_spot: { price: 3700, status: "ok" } } };
  assert.doesNotThrow(() => {
    const result = alignOracleValue(baseRow, oracleRowMissingHash, "oneinch_spot");
    assert.equal(result.price, null);
    assert.equal(result.status, "hash_mismatch");
  });
});



