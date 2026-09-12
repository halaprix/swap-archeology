/**
 * Static catalog loader and query helpers.
 * Reads directly from the self-contained public/data/catalog.json file.
 */

import catalogJson from "../../public/data/catalog.json";
import { Catalog, CatalogPin, CatalogReportEntry, CatalogToken, SwapReport } from "./types";
import { CANONICAL_TOKENS } from "./tokenMetadata";

export const STATIC_CATALOG = catalogJson as unknown as Catalog;

export function getPins(): CatalogPin[] {
  return STATIC_CATALOG.pins || [];
}

export function getTokens(): CatalogToken[] {
  const existing = STATIC_CATALOG.tokens || [];
  const seenAddresses = new Set(existing.map((t) => (t.address || "").toLowerCase().trim()));
  const list = [...existing];

  for (const [addr, meta] of Object.entries(CANONICAL_TOKENS)) {
    if (!seenAddresses.has(addr.toLowerCase().trim())) {
      list.push({
        symbol: meta.symbol,
        address: addr,
        decimals: meta.decimals,
      });
      seenAddresses.add(addr.toLowerCase().trim());
    }
  }

  return list;
}

export function getSavedReports(): CatalogReportEntry[] {
  return STATIC_CATALOG.reports || [];
}

export function getReportById(id: string): CatalogReportEntry | undefined {
  return STATIC_CATALOG.reports.find((r) => r.id === id);
}

export function getReportsForPin(blockNumber: string): CatalogReportEntry[] {
  return STATIC_CATALOG.reports.filter((r) => r.report.block === blockNumber);
}

/**
 * Creates a unique, exact key for amount + token identity.
 */
export function getAmountFilterKey(amountIn: string, tokenInAddressOrSymbol: string): string {
  return `${amountIn}:${(tokenInAddressOrSymbol || "").toLowerCase().trim()}`;
}

/**
 * Normalizes source subset or selected families into a deterministic sorted string.
 * Strictly handles object { sources: [] }, array [], or null without calling .join() on objects.
 */
export function normalizeSourceSet(report: SwapReport): string {
  if (!report) return "";

  const subset = report.source_subset;
  if (subset) {
    if (typeof subset === "object" && !Array.isArray(subset) && Array.isArray((subset as { sources: string[] }).sources)) {
      return (subset as { sources: string[] }).sources.slice().sort().join(",");
    }
    if (Array.isArray(subset)) {
      return subset.slice().sort().join(",");
    }
  }

  // Fallback to selected_families
  if (Array.isArray(report.selected_families)) {
    return report.selected_families.slice().sort().join(",");
  }

  return "";
}

/**
 * Filter reports by pin block, pair, exact amount key, and solver.
 */
export function filterReports(
  reports: CatalogReportEntry[],
  filters: {
    pinBlock?: string;
    pair?: string;
    amountKey?: string;
    solver?: string;
  }
): CatalogReportEntry[] {
  return reports.filter((entry) => {
    const rep = entry.report;
    if (filters.pinBlock && filters.pinBlock !== "all" && rep.block !== filters.pinBlock) {
      return false;
    }
    if (filters.pair && filters.pair !== "all") {
      const pairStr = `${rep.request.symbol_in} → ${rep.request.symbol_out}`;
      if (pairStr !== filters.pair) return false;
    }
    if (filters.amountKey && filters.amountKey !== "all") {
      // Must match BOTH exact raw amount_in AND token_in address
      const repKey = getAmountFilterKey(rep.request.amount_in, rep.request.token_in);
      if (repKey !== filters.amountKey) return false;
    }
    if (filters.solver && filters.solver !== "all" && rep.requested_solver !== filters.solver) {
      return false;
    }
    return true;
  });
}

/**
 * Find saved observations across blocks with identical pair, raw amount, solver, and source set.
 * Strictly avoids mixing different source-set observations.
 * Preserves duplicate block observations as explicit distinct results.
 */
export function findIdenticalObservations(
  current: SwapReport,
  allReports: CatalogReportEntry[]
): CatalogReportEntry[] {
  const cReq = current.request;
  const cSolver = current.requested_solver;
  const cSourcesNormalized = normalizeSourceSet(current);

  return allReports.filter((entry) => {
    const other = entry.report;
    if (other.request.token_in.toLowerCase().trim() !== cReq.token_in.toLowerCase().trim()) return false;
    if (other.request.token_out.toLowerCase().trim() !== cReq.token_out.toLowerCase().trim()) return false;
    if (other.request.amount_in !== cReq.amount_in) return false;
    if (other.requested_solver !== cSolver) return false;

    const otherSourcesNormalized = normalizeSourceSet(other);
    if (otherSourcesNormalized !== cSourcesNormalized) return false;

    return true;
  }).sort((a, b) => {
    // Sort chronologically by block number, then deterministically by ID
    const blockA = BigInt(a.report.block);
    const blockB = BigInt(b.report.block);
    if (blockA < blockB) return -1;
    if (blockA > blockB) return 1;
    return a.id.localeCompare(b.id);
  });
}
