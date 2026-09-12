import { bestDirectPool, type Pool, type Quote } from "./octoberSources";
import { getVenueInfo } from "./venues";

export type Size = "1" | "10" | "100";
export type Aggregate = Record<"ETH" | "WETH", Record<Size, number | null>>;
export type Availability = { status: string; usableCount: number; directCount: number };

export type LiquidityGapRow = {
  block: number;
  blockHash: string;
  timestamp: string;
  chainlink: number | null;
  aave: number | null;
  aggregates: Aggregate;
  aggregateRefunds?: Record<"ETH" | "WETH", Record<Size, Record<string, number> | null>>;
  pools: Record<string, Record<Size, Quote>>;
  availability: Record<string, Availability>;
};

export type LiquidityGapScope = {
  startBlock?: number;
  endBlock?: number;
  startUtc?: string;
  endUtc?: string;
  stride: number;
  sizes: Size[];
};

export type LiquidityGapData = {
  schemaVersion: number;
  scope: LiquidityGapScope;
  pools: Pool[];
  families: Array<{ id: string; label: string }>;
  rows: LiquidityGapRow[];
};

export type CrashStatus = "pending" | "partial" | "collected" | "failed";

export type CrashCoverage = {
  blockCount?: number;
  expectedBlocks?: number;
  stride?: number;
  missingBlocks?: number[];
  usableFeedCount?: number;
  totalFeedCount?: number;
  notes?: string;
  [key: string]: unknown;
};

export type CrashIndexEntry = {
  id: string;
  label: string;
  crashUtc: string;
  startUtc: string;
  endUtc: string;
  status: CrashStatus | string;
  sourcesUrl: string;
  oracleReferencesUrl?: string;
  csvUrl?: string;
  coverage?: CrashCoverage;
  blockCount?: number;
  expectedBlocks?: number;
  stride?: number;
  [key: string]: unknown;
};

export type FiveCrashIndex = {
  schemaVersion: number;
  crashes: CrashIndexEntry[];
  [key: string]: unknown;
};

export const DEFAULT_FIVE_CRASHES: CrashIndexEntry[] = [
  {
    id: "crash-1",
    label: "2025-02-03",
    crashUtc: "2025-02-03T01:56:59Z",
    startUtc: "2025-02-03T01:26:59Z",
    endUtc: "2025-02-03T02:26:59Z",
    status: "pending",
    sourcesUrl: "/five-crash-liquidity/crash-1/sources.json",
    oracleReferencesUrl: "/five-crash-liquidity/crash-1/oracle-references.json",
    csvUrl: "/five-crash-liquidity/crash-1/sources.csv",
    coverage: {
      notes: "Collection pending. Crash anchor is the close-second of the Binance minute containing the selected hourly low.",
    },
  },
  {
    id: "crash-2",
    label: "2025-02-25",
    crashUtc: "2025-02-25T07:25:59Z",
    startUtc: "2025-02-25T06:55:59Z",
    endUtc: "2025-02-25T07:55:59Z",
    status: "pending",
    sourcesUrl: "/five-crash-liquidity/crash-2/sources.json",
    oracleReferencesUrl: "/five-crash-liquidity/crash-2/oracle-references.json",
    csvUrl: "/five-crash-liquidity/crash-2/sources.csv",
    coverage: {
      notes: "Collection pending. Crash anchor is the close-second of the Binance minute containing the selected hourly low.",
    },
  },
  {
    id: "crash-3",
    label: "2025-04-07",
    crashUtc: "2025-04-07T06:54:59Z",
    startUtc: "2025-04-07T06:24:59Z",
    endUtc: "2025-04-07T07:24:59Z",
    status: "pending",
    sourcesUrl: "/five-crash-liquidity/crash-3/sources.json",
    oracleReferencesUrl: "/five-crash-liquidity/crash-3/oracle-references.json",
    csvUrl: "/five-crash-liquidity/crash-3/sources.csv",
    coverage: {
      notes: "Collection pending. Crash anchor is the close-second of the Binance minute containing the selected hourly low.",
    },
  },
  {
    id: "crash-4",
    label: "2025-06-21",
    crashUtc: "2025-06-21T21:31:59Z",
    startUtc: "2025-06-21T21:01:59Z",
    endUtc: "2025-06-21T22:01:59Z",
    status: "pending",
    sourcesUrl: "/five-crash-liquidity/crash-4/sources.json",
    oracleReferencesUrl: "/five-crash-liquidity/crash-4/oracle-references.json",
    csvUrl: "/five-crash-liquidity/crash-4/sources.csv",
    coverage: {
      notes: "Collection pending. Crash anchor is the close-second of the Binance minute containing the selected hourly low.",
    },
  },
  {
    id: "crash-5",
    label: "2025-10-10",
    crashUtc: "2025-10-10T21:20:59Z",
    startUtc: "2025-10-10T21:14:00Z",
    endUtc: "2025-10-10T22:14:00Z",
    status: "pending",
    sourcesUrl: "/five-crash-liquidity/crash-5/sources.json",
    oracleReferencesUrl: "/five-crash-liquidity/crash-5/oracle-references.json",
    csvUrl: "/five-crash-liquidity/crash-5/sources.csv",
    coverage: {
      notes: "Collection pending. Crash anchor is the close-second of the Binance minute containing the selected hourly low.",
    },
  },
];

export function normalizeCrashStatus(status: unknown): CrashStatus {
  if (typeof status !== "string") return "pending";
  const s = status.toLowerCase().trim();
  if (s === "collected" || s === "complete" || s === "completed") return "collected";
  if (s === "partial") return "partial";
  if (s === "failed" || s === "error") return "failed";
  return "pending";
}

export function statusBadgeVariant(status: string): "neutral" | "warning" | "success" | "error" {
  const norm = normalizeCrashStatus(status);
  switch (norm) {
    case "collected":
      return "success";
    case "partial":
      return "warning";
    case "failed":
      return "error";
    case "pending":
    default:
      return "neutral";
  }
}

export function statusDescription(status: string): string {
  const norm = normalizeCrashStatus(status);
  switch (norm) {
    case "collected":
      return "Collected";
    case "partial":
      return "Partial collection";
    case "failed":
      return "Collection failed";
    case "pending":
    default:
      return "Collection pending";
  }
}

export function parseFiveCrashIndex(data: unknown): FiveCrashIndex | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as Record<string, unknown>;
  if (obj.schemaVersion !== 1 || !Array.isArray(obj.crashes)) return null;

  const validCrashes: CrashIndexEntry[] = [];
  for (const item of obj.crashes) {
    if (!item || typeof item !== "object") return null;
    const c = item as Record<string, unknown>;
    if (typeof c.id !== "string" || !c.id) return null;
    if (typeof c.sourcesUrl !== "string" || !c.sourcesUrl) return null;

    validCrashes.push({
      id: c.id,
      label: typeof c.label === "string" ? c.label : c.id,
      crashUtc: typeof c.crashUtc === "string" ? c.crashUtc : "",
      startUtc: typeof c.startUtc === "string" ? c.startUtc : "",
      endUtc: typeof c.endUtc === "string" ? c.endUtc : "",
      status: typeof c.status === "string" ? c.status : "pending",
      sourcesUrl: c.sourcesUrl,
      oracleReferencesUrl: typeof c.oracleReferencesUrl === "string" ? c.oracleReferencesUrl : undefined,
      csvUrl: typeof c.csvUrl === "string" ? c.csvUrl : undefined,
      coverage: c.coverage && typeof c.coverage === "object" ? (c.coverage as CrashCoverage) : undefined,
      blockCount: typeof c.blockCount === "number" ? c.blockCount : undefined,
      expectedBlocks: typeof c.expectedBlocks === "number" ? c.expectedBlocks : undefined,
      stride: typeof c.stride === "number" ? c.stride : undefined,
    });
  }

  return {
    schemaVersion: 1,
    crashes: validCrashes,
  };
}

export function familyBest(rows: LiquidityGapRow[], pools: Pool[], family: string, size: Size): Array<number | null> {
  const candidates = pools.filter((pool) => pool.family === family);
  return rows.map((row) => bestDirectPool(candidates, Object.fromEntries(candidates.map((pool) => [pool.id, row.pools[pool.id]?.[size]])))?.price ?? null);
}

export function getFamilyLabel(id: string, families?: Array<{ id: string; label: string }>): string {
  const entry = families?.find((f) => f.id === id);
  if (entry && entry.label && entry.label !== entry.id) return entry.label;
  return getVenueInfo(id).name;
}
