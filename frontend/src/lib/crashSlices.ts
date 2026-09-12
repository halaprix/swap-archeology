export type OracleStatus = "ok" | "unsupported_direct_market_feed" | "missing_round" | "invalid_round";

export interface CrashSliceRow {
  sliceId: string;
  block: number;
  blockHash: string;
  timestampISO: string;
  tokenIn: string;
  tokenOut: string;
  amount: string;
  amountRaw: string;
  aggregatedPrice: number | null;
  singlePoolBaselinePrice?: number | null;
  oraclePrice: number | null;
  deviationBps: number | null;
  oracleStatus: OracleStatus;
  oracleAgeSeconds: number | null;
  report: Record<string, unknown>;
  displayReport?: import("@/lib/types").SwapReport;
  sourceCoverage: {
    selectedFamilies?: string[];
    usablePools?: number | string;
    unsupported?: unknown[];
  };
}

export interface CrashSlicesData {
  schemaVersion: 1;
  generatedAt: string;
  qualificationMode: "collection-model-only";
  slices: Array<{ id: string; label: string; anchorBlock: number; cadenceSeconds: number; blocks: number[] }>;
  feeds: Array<{ pair: string; address: string; decimals: number; semantics: string; evidence: string }>;
  rows: CrashSliceRow[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const ORACLE_STATUSES = new Set<OracleStatus>(["ok", "unsupported_direct_market_feed", "missing_round", "invalid_round"]);

function finiteOrNull(value: unknown): number | null | undefined {
  return value === null ? null : typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function timestampString(value: unknown): string | null {
  if (typeof value === "string" && !Number.isNaN(Date.parse(value))) return value;
  if (typeof value === "number" && Number.isFinite(value)) {
    const milliseconds = value < 1_000_000_000_000 ? value * 1000 : value;
    const date = new Date(milliseconds);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }
  return null;
}

function normalizeRow(value: unknown): CrashSliceRow | null {
  if (!isRecord(value) || typeof value.sliceId !== "string" || typeof value.block !== "number"
      || typeof value.blockHash !== "string" || typeof value.tokenIn !== "string" || typeof value.tokenOut !== "string"
      || typeof value.amount !== "string" || !isRecord(value.report) || !isRecord(value.sourceCoverage)
      || typeof value.oracleStatus !== "string" || !ORACLE_STATUSES.has(value.oracleStatus as OracleStatus)) return null;
  // v1 originally exported this ISO value as `timestamp`; accept the explicit
  // compatibility spelling while normalizing the UI model to `timestampISO`.
  const timestampISO = timestampString(value.timestampISO ?? value.timestamp);
  const aggregatedPrice = finiteOrNull(value.aggregatedPrice);
  const singlePoolBaselinePrice = finiteOrNull(value.singlePoolBaselinePrice);
  const oraclePrice = finiteOrNull(value.oraclePrice);
  const deviationBps = finiteOrNull(value.deviationBps);
  const oracleAgeSeconds = finiteOrNull(value.oracleAgeSeconds);
  if (timestampISO === null || aggregatedPrice === undefined || singlePoolBaselinePrice === undefined
      || oraclePrice === undefined || deviationBps === undefined || oracleAgeSeconds === undefined
      || (value.displayReport !== undefined && !isRecord(value.displayReport))) return null;
  const coverage = value.sourceCoverage;
  return {
    sliceId: value.sliceId, block: value.block, blockHash: value.blockHash, timestampISO,
    tokenIn: value.tokenIn, tokenOut: value.tokenOut, amount: value.amount,
    amountRaw: String(value.amountRaw), aggregatedPrice, singlePoolBaselinePrice, oraclePrice,
    deviationBps, oracleStatus: value.oracleStatus as OracleStatus, oracleAgeSeconds,
    report: value.report, displayReport: value.displayReport as CrashSliceRow["displayReport"],
    sourceCoverage: {
      selectedFamilies: Array.isArray(coverage.selectedFamilies) ? coverage.selectedFamilies.filter((item): item is string => typeof item === "string") : [],
      usablePools: typeof coverage.usablePools === "number" || typeof coverage.usablePools === "string" ? coverage.usablePools : undefined,
      unsupported: Array.isArray(coverage.unsupported) ? coverage.unsupported : [],
    },
  };
}

export function parseCrashSlices(value: unknown): CrashSlicesData | null {
  if (!isRecord(value) || value.schemaVersion !== 1 || value.qualificationMode !== "collection-model-only"
      || !Array.isArray(value.rows) || !Array.isArray(value.slices) || !Array.isArray(value.feeds)
      || typeof value.generatedAt !== "string") return null;
  const rows = value.rows.map(normalizeRow);
  if (rows.some((row) => row === null)) return null;
  return { ...value, rows: rows as CrashSliceRow[] } as unknown as CrashSlicesData;
}

export function pairKey(row: CrashSliceRow): string {
  return `${row.tokenIn} → ${row.tokenOut}`;
}

export function amountKey(row: CrashSliceRow): string {
  return `${row.amountRaw}:${row.tokenIn}`;
}

export function chronological(rows: CrashSliceRow[]): CrashSliceRow[] {
  return [...rows].sort((a, b) => a.timestampISO.localeCompare(b.timestampISO) || a.block - b.block);
}
