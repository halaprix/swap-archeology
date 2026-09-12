"use client";

import React, { useMemo } from "react";
import { CatalogReportEntry } from "@/lib/types";
import { Select } from "@/components/ui/Select";
import { formatRawUnits, shortenHash } from "@/lib/formatting";
import { getAmountFilterKey } from "@/lib/catalog";

export interface ReportFilterState {
  pair: string;
  amountKey: string; // exact raw amount + token_in key
  solver: string;
}

export interface ReportFiltersProps {
  reports: CatalogReportEntry[];
  allAvailableReports?: CatalogReportEntry[];
  selectedReportId: string;
  onSelectReport: (id: string) => void;
  filters: ReportFilterState;
  onFilterChange: (filters: ReportFilterState) => void;
}

export const ReportFilters: React.FC<ReportFiltersProps> = ({
  reports,
  allAvailableReports,
  selectedReportId,
  onSelectReport,
  filters,
  onFilterChange,
}) => {
  const sourceList = allAvailableReports && allAvailableReports.length > 0 ? allAvailableReports : reports;

  // Extract unique pairs
  const pairOptions = useMemo(() => {
    const pairs = Array.from(
      new Set(sourceList.map((r) => `${r.report.request.symbol_in} → ${r.report.request.symbol_out}`))
    ).sort();
    return [
      { value: "all", label: "All Pairs" },
      ...pairs.map((pair) => ({ value: pair, label: pair })),
    ];
  }, [sourceList]);

  // Extract unique amounts using exact raw amount + token identity keys
  const amountOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const r of sourceList) {
      const req = r.report.request;
      const key = getAmountFilterKey(req.amount_in, req.token_in);
      if (!seen.has(key)) {
        const formatted = formatRawUnits(req.amount_in, req.decimals_in, 4);
        seen.set(key, `${formatted} ${req.symbol_in}`);
      }
    }
    const options = Array.from(seen.entries()).map(([key, label]) => ({
      value: key,
      label,
    }));
    return [{ value: "all", label: "All Input Amounts" }, ...options];
  }, [sourceList]);

  // Dynamically derive solver choices from actual records in catalog (baseline, search, dual)
  const solverOptions = useMemo(() => {
    const solvers = Array.from(new Set(sourceList.map((r) => r.report.requested_solver))).sort();
    return [
      { value: "all", label: "All Solvers" },
      ...solvers.map((s) => {
        let label = s.toUpperCase();
        if (s === "baseline") label = "Baseline (Bounded Two-Hop)";
        else if (s === "search") label = "Search (Multi-Split/Multi-Hop)";
        else if (s === "dual") label = "Dual (Numerical Estimate)";
        return { value: s, label };
      }),
    ];
  }, [sourceList]);

  const hasMatching = reports.length > 0;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
        gap: "var(--space-3)",
        alignItems: "flex-end",
        padding: "var(--space-3)",
        backgroundColor: "var(--color-surface-muted)",
        borderRadius: "var(--radius-sm)",
        border: "1px solid var(--color-border)",
      }}
    >
      <Select
        label="Pair Filter"
        value={filters.pair}
        onChange={(e) => onFilterChange({ ...filters, pair: e.target.value })}
        options={pairOptions}
      />

      <Select
        label="Input Amount"
        value={filters.amountKey}
        onChange={(e) => onFilterChange({ ...filters, amountKey: e.target.value })}
        options={amountOptions}
      />

      <Select
        label="Solver Type"
        value={filters.solver}
        onChange={(e) => onFilterChange({ ...filters, solver: e.target.value })}
        options={solverOptions}
      />

      <Select
        label={`Matching Reports (${reports.length})`}
        value={hasMatching ? selectedReportId : ""}
        disabled={!hasMatching}
        onChange={(e) => onSelectReport(e.target.value)}
      >
        {!hasMatching ? (
          <option value="">0 matching reports</option>
        ) : (
          reports.map((entry) => {
            const rep = entry.report;
            const symIn = rep.request.symbol_in;
            const symOut = rep.request.symbol_out;
            const inAmt = formatRawUnits(rep.request.amount_in, rep.request.decimals_in, 0);
            const outAmt = rep.best_split
              ? formatRawUnits(rep.best_split.amount_out, rep.request.decimals_out, 2)
              : "No route";
            return (
              <option key={entry.id} value={entry.id}>
                Block #{rep.block} · {inAmt} {symIn} → {outAmt} {symOut} ({rep.requested_solver}, {shortenHash(entry.id, 6, 4)})
              </option>
            );
          })
        )}
      </Select>
    </div>
  );
};
