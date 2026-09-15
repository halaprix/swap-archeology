export type Quote = { price: number | null; amountOut: string | null; reason?: string };
export type Pool = { id: string; family: string; address: string; inputSymbol: string; feeBps: number | null; label: string };

export type OracleSourceMeta = {
  id: string;
  label: string;
  kind: "spot" | "twap" | string;
  description: string;
  windowSeconds?: number;
  address?: string;
  pool?: string;
};

export type OracleValue = {
  price: number | null;
  status: string;
  reason?: string;
};

export type OracleRow = {
  block: number;
  blockHash: string;
  timestamp?: string;
  values: Record<string, OracleValue>;
};

export type OracleReferencesData = {
  schemaVersion: number;
  sources: OracleSourceMeta[];
  rows: OracleRow[];
};

export type BaseRowIdentity = {
  block: number;
  blockHash: string;
};

export type AlignedOracleValue = {
  price: number | null;
  status: string;
  reason?: string;
};

export type OracleSourceConfig = {
  id: string;
  label: string;
  kind: "spot" | "twap" | "oracle" | "market";
  windowSeconds?: number;
  description: string;
  color: string;
  dash?: string;
  defaultVisible: boolean;
};

export const KNOWN_ORACLE_SOURCES: Record<string, OracleSourceConfig> = {
  binance_eth_usdc: {
    id: "binance_eth_usdc", label: "Binance ETH/USDC · 1m close", kind: "market",
    description: "Most recent completed Binance ETHUSDC minute close; no future candle or USDT substitution.",
    color: "#ca8a04", dash: "10 5", windowSeconds: 60, defaultVisible: true,
  },
  coingecko_eth_usdc: {
    id: "coingecko_eth_usdc", label: "CoinGecko ETH · hourly / Chainlink USDC", kind: "market",
    description: "Hourly ETH/USD observations converted with Chainlink USDC/USD. Last timestamped observation carried forward; coarse historical reference.",
    color: "#65a30d", dash: "3 5", windowSeconds: 3600, defaultVisible: true,
  },
  chronicle_eth_usdc: {
    id: "chronicle_eth_usdc",
    label: "Chronicle ETH / Chainlink USDC",
    kind: "oracle",
    description: "Exposed Chronicle ScribeOptimistic ETH/USD value divided by same-block Chainlink USDC/USD. Read age and challenge period appear below; pending updates are excluded.",
    color: "#7c3aed",
    defaultVisible: true,
  },
  chaos_avalanche_eth_usdc: {
    id: "chaos_avalanche_eth_usdc",
    label: "Chaos WETH (Avalanche) / Chainlink USDC",
    kind: "oracle",
    description: "Avalanche Chaos WETH/USD divided by Ethereum Chainlink USDC/USD, aligned backwards by UTC. Cross-chain reference; update ages appear below.",
    color: "#ea580c",
    defaultVisible: true,
  },
  chaos_eth_usdc: {
    id: "chaos_eth_usdc",
    label: "Chaos ETH/USD (unresolved)",
    kind: "oracle",
    description: "No verified Ethereum ETH/USD feed address for October 10, 2025. The verified historical candidate reports ETHFI/USD, a different asset.",
    color: "#64748b",
    defaultVisible: false,
  },
  redstone_eth_usdc: {
    id: "redstone_eth_usdc",
    label: "RedStone ETH / Chainlink USDC",
    kind: "oracle",
    description: "RedStone ETH/USD divided by same-block Chainlink USDC/USD. Mixed-provider USDC-per-ETH reference, independent of swap size. Update ages appear in the reference table.",
    color: "#dc2626",
    defaultVisible: true,
  },
  oneinch_spot: {
    id: "oneinch_spot",
    label: "1inch spot (liquidity-weighted reference)",
    kind: "spot",
    description: "1inch Spot price aggregator liquidity-weighted reference (USDC per WETH; useWrappers=false). Reference benchmark, not an executable routing quote; sidecar provides underlying connector configuration.",
    color: "#0891b2",
    defaultVisible: true,
  },
  uniswap_v3_twap_300: {
    id: "uniswap_v3_twap_300",
    label: "Uniswap v3 300s TWAP (reference)",
    kind: "twap",
    windowSeconds: 300,
    description: "Uniswap v3 geometric mean time-weighted average price over 300 seconds (5 minutes) from pool observations. On-chain benchmark reference, not an executable quote or external oracle.",
    color: "#be185d",
    defaultVisible: true,
  },
  uniswap_v3_twap_60: {
    id: "uniswap_v3_twap_60",
    label: "Uniswap v3 60s TWAP (reference)",
    kind: "twap",
    windowSeconds: 60,
    description: "Uniswap v3 geometric mean time-weighted average price over 60 seconds (1 minute) from pool observations. On-chain benchmark reference, not an executable quote or external oracle.",
    color: "#9a3412",
    dash: "4 2",
    defaultVisible: false,
  },
};

export function bestDirectPool(pools: Pool[], quotes: Record<string, Quote> | undefined): { id: string; price: number } | null {
  let best: { id: string; price: number } | null = null;
  for (const pool of pools) {
    const price = quotes?.[pool.id]?.price;
    if (typeof price === "number" && Number.isFinite(price) && price > 0 && (!best || price > best.price)) best = { id: pool.id, price };
  }
  return best;
}

export function linePath<T>(rows: T[], point: (row: T, index: number) => { x: number; y: number; value: number | null }): string {
  let path = "", connected = false;
  rows.forEach((row, index) => {
    const pointAtRow = point(row, index);
    if (typeof pointAtRow.value === "number" && Number.isFinite(pointAtRow.value)) {
      path += `${connected ? "L" : "M"}${pointAtRow.x},${pointAtRow.y} `;
      connected = true;
    } else connected = false;
  });
  return path;
}

export function alignOracleValue(
  baseRow: BaseRowIdentity,
  oracleRow: OracleRow | undefined,
  sourceId: string
): AlignedOracleValue {
  if (!oracleRow) {
    return { price: null, status: "missing_block", reason: `Block #${baseRow.block} not in reference dataset` };
  }
  if (baseRow.block !== oracleRow.block) {
    return {
      price: null,
      status: "block_mismatch",
      reason: `Block number mismatch (base: #${baseRow.block}, oracle: #${oracleRow.block})`,
    };
  }
  if (
    !baseRow.blockHash ||
    !oracleRow.blockHash ||
    baseRow.blockHash.toLowerCase() !== oracleRow.blockHash.toLowerCase()
  ) {
    const baseSummary = baseRow.blockHash ? `${baseRow.blockHash.slice(0, 10)}...` : "missing";
    const oracleSummary = oracleRow.blockHash ? `${oracleRow.blockHash.slice(0, 10)}...` : "missing";
    return {
      price: null,
      status: "hash_mismatch",
      reason: `Block hash mismatch at #${baseRow.block} (base: ${baseSummary}, oracle: ${oracleSummary})`,
    };
  }
  const val = oracleRow.values?.[sourceId];
  if (!val) {
    return { price: null, status: "missing_source", reason: `Source ${sourceId} missing at #${baseRow.block}` };
  }
  const isSuccessStatus = val.status === "ok" || val.status === "available";
  const price = isSuccessStatus && typeof val.price === "number" && Number.isFinite(val.price) && val.price > 0
    ? val.price
    : null;
  return {
    price,
    status: val.status || (price !== null ? "available" : "unavailable"),
    reason: val.reason,
  };
}

export function alignOracleSeries(
  baseRows: BaseRowIdentity[],
  oracleData: OracleReferencesData | null | undefined,
  sourceId: string
): AlignedOracleValue[] {
  if (!oracleData || !Array.isArray(oracleData.rows)) {
    return baseRows.map(() => ({
      price: null,
      status: "unloaded",
      reason: "Oracle reference sidecar not loaded",
    }));
  }

  // Index by exact block number - NEVER align by array index!
  const map = new Map<number, OracleRow>();
  for (const row of oracleData.rows) {
    if (typeof row?.block === "number") {
      map.set(row.block, row);
    }
  }

  return baseRows.map((baseRow) => alignOracleValue(baseRow, map.get(baseRow.block), sourceId));
}
