/**
 * Swap Archeology - Core Type Definitions
 * Strict schemas for exported catalog reports, API payloads, and routing graph models.
 */

export interface CatalogPin {
  number: string;
  hash: string;
  timestamp: string;
  label: string;
}

export interface CatalogToken {
  symbol: string;
  address: string;
  decimals: number;
}

export interface RouteStep {
  pool_id: string;
  token_in: string;
  token_out: string;
  amount_in: string; // raw integer string, never JS Number
  amount_out: string; // raw integer string, never JS Number
}

export interface SearchAllocationPath {
  path: string[];
  amount_in: string;
}

export interface SearchInfo {
  kind: string; // e.g. "split" or "bounded_stateful_search"
  allocation: SearchAllocationPath[] | null;
}

export type SourceSubset =
  | string[]
  | {
      recomputed?: boolean;
      sources: string[];
    }
  | null;

export interface BestSplit {
  amount_out: string; // raw integer string
  steps: RouteStep[];
  search_info: SearchInfo;
  gas_estimate: string | null;
}

export interface SinglePoolBaseline {
  amount_out: string; // raw integer string
}

export interface QuoteRequestDetails {
  token_in: string;
  token_out: string;
  symbol_in: string;
  symbol_out: string;
  decimals_in: string;
  decimals_out: string;
  amount_in: string; // raw integer string
}

export interface SourceCoverage {
  family: string;
  status: "supported" | "discovered_unsupported" | "unavailable" | "not_deployed_at_block" | string;
  selected: boolean;
  discovered_pools: string;
  usable_pools: string;
}

export interface UnsupportedReason {
  family: string;
  status: string;
  reason: string;
}

export interface SwapReport {
  chain: string;
  block: string;
  block_hash: string;
  timestamp: string;
  request: QuoteRequestDetails;
  single_pool_baseline: SinglePoolBaseline | null;
  best_split: BestSplit | null; // null means no feasible route
  requested_solver: "baseline" | "search" | "dual" | string;
  selected_families: string[];
  source_subset: SourceSubset;
  sources: SourceCoverage[];
  unsupported: UnsupportedReason[];
  limitations: string[];
}

export interface CatalogReportEntry {
  id: string; // sha256 hex
  origin: "saved" | "adhoc";
  report: SwapReport;
  cached?: boolean;
}

export interface Catalog {
  schemaVersion: number;
  pins: CatalogPin[];
  tokens: CatalogToken[];
  reports: CatalogReportEntry[];
}

export interface QuoteApiRequest {
  block: string;
  tokenIn: string;
  tokenOut: string;
  amount: string; // decimal string, e.g. "100"
  solver: "baseline" | "search";
  sources?: string[];
}

export interface QuoteApiResponseSuccess {
  id: string;
  origin: "adhoc";
  report: SwapReport;
  cached: boolean;
}

export interface ApiErrorResponse {
  error: {
    code: string;
    message: string;
  };
}

export interface ReportsApiResponse {
  reports: CatalogReportEntry[];
}
