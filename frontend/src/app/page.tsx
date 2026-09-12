"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CatalogReportEntry } from "@/lib/types";
import { getPins, getSavedReports } from "@/lib/catalog";
import { PinnedBlockTabs } from "@/components/history/PinnedBlockTabs";
import { ReportFilters, ReportFilterState } from "@/components/history/ReportFilters";
import { ReportDetail } from "@/components/history/ReportDetail";
import { filterReports } from "@/lib/catalog";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";

const RESEARCH_VIEWS = [
  {
    href: "#saved-routes",
    title: "Saved routes",
    badge: "Catalog",
    description: "Inspect exported historical reports by pinned block, pair, amount, solver, route steps, and source coverage.",
  },
  {
    href: "/october-gap",
    title: "October gap",
    badge: "254 blocks",
    description: "Compare direct ETH/WETH-to-USDC pool quotes with recorded reference series on October 10, 2025.",
  },
  {
    href: "/crash-gaps",
    title: "Five-crash comparisons",
    badge: "5 windows",
    description: "Browse direct-liquidity observations across five Binance-derived crash windows and their stated coverage.",
  },
  {
    href: "/crash-slices",
    title: "Crash slices",
    badge: "Saved",
    description: "Review 48 saved multi-source routing observations at 16 historical crash blocks.",
  },
  {
    href: "/quote-lab",
    title: "Quote lab",
    badge: "Optional backend",
    description: "Run an ad hoc evaluation only when the server-side cached-state Python bridge is configured.",
  },
  {
    href: "/october-swap-events/index.html",
    title: "October swap events",
    badge: "Standalone",
    description: "Open the decoded six-pool swap-event diagnostic for 50 October blocks; it is not an overlay on the quote chart.",
    standalone: true,
  },
  {
    href: "/design-system",
    title: "Design system",
    badge: "Secondary",
    description: "View the shared research-workspace tokens and UI primitives used throughout the explorer.",
  },
];

export default function HistoricalWorkspace() {
  const pins = useMemo(() => getPins(), []);
  const initialReports = useMemo(() => getSavedReports(), []);

  const [allReports, setAllReports] = useState<CatalogReportEntry[]>(initialReports);
  const [selectedBlock, setSelectedBlock] = useState<string>("25896003"); // Default to calm control pin

  const [filters, setFilters] = useState<ReportFilterState>({
    pair: "all",
    amountKey: "all",
    solver: "all",
  });

  // Try fetching any stored ad-hoc reports from the local proxy on load
  useEffect(() => {
    async function loadAdHocReports() {
      try {
        const res = await fetch("/api/reports");
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.reports) && data.reports.length > 0) {
            setAllReports((prev) => {
              const existingIds = new Set(prev.map((r) => r.id));
              const newEntries = data.reports.filter((r: CatalogReportEntry) => !existingIds.has(r.id));
              return [...prev, ...newEntries];
            });
          }
        }
      } catch {
        // Quiet fallback to static catalog
      }
    }
    loadAdHocReports();
  }, []);

  const filteredReports = useMemo(
    () => filterReports(allReports, { pinBlock: selectedBlock, ...filters }),
    [allReports, selectedBlock, filters]
  );

  // User-selected report ID
  const [selectedReportId, setSelectedReportId] = useState<string>(() => {
    const defaultEntry = initialReports.find((r) => r.report.block === "25896003") || initialReports[0];
    return defaultEntry?.id || "";
  });

  // Pure derived active report: when filteredReports is empty, strictly null!
  const activeReportEntry = useMemo(() => {
    if (filteredReports.length === 0) {
      return null;
    }
    const matched = filteredReports.find((r) => r.id === selectedReportId);
    return matched || filteredReports[0];
  }, [filteredReports, selectedReportId]);

  const handleSelectPin = (blockNumber: string) => {
    setSelectedBlock(blockNumber);
    // If the active filter has reports for that block, pick the first
    const match = allReports.find((r) => r.report.block === blockNumber);
    if (match) {
      setSelectedReportId(match.id);
    }
  };

  return (
    <div
      style={{
        maxWidth: "1440px",
        width: "100%",
        margin: "0 auto",
        padding: "var(--space-6)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-6)",
      }}
    >
      {/* Workbench Introduction Banner */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          flexWrap: "wrap",
          gap: "var(--space-3)",
          borderBottom: "1px solid var(--color-border)",
          paddingBottom: "var(--space-4)",
        }}
      >
        <div>
          <h1
            style={{
              fontSize: "var(--font-size-2xl)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
              letterSpacing: "-0.02em",
              lineHeight: "1.2",
            }}
          >
            Historical Routing Archeology
          </h1>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
              marginTop: "var(--space-1)",
              maxWidth: "760px",
            }}
          >
            Empirical liquidity and route solver analysis on historical Ethereum mainnet state snapshots. Inspect exact multi-pool splits, solver candidate evaluations, and protocol coverage across canonical pinned blocks.
          </p>
        </div>

        <div
          className="font-mono"
          style={{
            fontSize: "var(--font-size-xs)",
            color: "var(--color-text-muted)",
            textAlign: "right",
          }}
        >
          <div>Pinned Anchors: {pins.length} Blocks</div>
          <div>Catalog: {allReports.length} Reports</div>
        </div>
      </div>

      <section aria-labelledby="research-index-title" style={{ display: "grid", gap: "var(--space-3)" }}>
        <div>
          <h2 id="research-index-title" style={{ fontSize: "var(--font-size-lg)", color: "var(--color-text)" }}>
            Research index
          </h2>
          <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-secondary)", marginTop: "var(--space-1)" }}>
            Choose a published dataset or the saved-route explorer. Each view keeps its own coverage and interpretation limits visible.
          </p>
        </div>

        <div className="research-index-grid">
          {RESEARCH_VIEWS.map((view) => {
            const content = (
              <>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)", alignItems: "flex-start" }}>
                  <h3 style={{ fontSize: "var(--font-size-md)", color: "var(--color-text)" }}>{view.title}</h3>
                  <Badge variant={view.badge === "Optional backend" ? "warning" : "neutral"}>{view.badge}</Badge>
                </div>
                <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>{view.description}</p>
                <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-teal)", fontWeight: "var(--font-weight-semibold)" }}>
                  Open view →
                </span>
              </>
            );

            const style: React.CSSProperties = {
              textDecoration: "none",
              color: "inherit",
              display: "block",
              minWidth: 0,
            };

            return view.standalone ? (
              <a key={view.href} href={view.href} style={style}>
                <Card style={{ height: "100%" }}>{content}</Card>
              </a>
            ) : (
              <Link key={view.href} href={view.href} style={style}>
                <Card style={{ height: "100%" }}>{content}</Card>
              </Link>
            );
          })}
        </div>

        <Card variant="muted" style={{ gap: "var(--space-2)" }}>
          <h3 style={{ fontSize: "var(--font-size-md)", color: "var(--color-text)" }}>Data modes</h3>
          <div className="research-data-modes">
            <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
              <strong>Bundled static data.</strong> Saved reports, charts, CSV exports, and the October event diagnostic are included with the frontend and browse without a Python service.
            </p>
            <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
              <strong>Optional cached-state backend.</strong> Quote Lab uses a server-only bridge when configured to evaluate qualified local snapshots and reopen stored ad hoc results. It makes no browser RPC calls.
            </p>
          </div>
        </Card>
      </section>

      <section id="saved-routes" aria-labelledby="saved-routes-title" style={{ display: "grid", gap: "var(--space-4)", scrollMarginTop: "var(--space-6)" }}>
        <div>
          <h2 id="saved-routes-title" style={{ fontSize: "var(--font-size-lg)", color: "var(--color-text)" }}>
            Saved-route explorer
          </h2>
          <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-secondary)", marginTop: "var(--space-1)" }}>
            Filter and inspect the bundled historical route reports below.
          </p>
        </div>

        {/* Pinned Block Tabs (5 Historical Pins) */}
        <PinnedBlockTabs
          pins={pins}
          selectedBlock={selectedBlock}
          onSelectPin={handleSelectPin}
        />

        {/* Filter Toolbar */}
        <ReportFilters
          reports={filteredReports}
          allAvailableReports={allReports}
          selectedReportId={selectedReportId}
          onSelectReport={setSelectedReportId}
          filters={filters}
          onFilterChange={setFilters}
        />

        {/* Main Report Inspection View */}
        {activeReportEntry ? (
          <ReportDetail
            entry={activeReportEntry}
            allReports={allReports}
            onSelectReport={setSelectedReportId}
          />
        ) : (
          <div
            role="status"
            style={{
              padding: "var(--space-8)",
              textAlign: "center",
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-sm)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "var(--space-2)",
            }}
          >
            <div style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--color-text)", fontSize: "var(--font-size-md)" }}>
              No Matching Saved Reports Found
            </div>
            <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)", maxWidth: "480px" }}>
              No reports in the catalog match the current selection (Block #{selectedBlock}, Pair: {filters.pair}, Amount: {filters.amountKey}, Solver: {filters.solver}). Reset or modify filters to view observations.
            </p>
            <button
              type="button"
              onClick={() =>
                setFilters({
                  pair: "all",
                  amountKey: "all",
                  solver: "all",
                })
              }
              style={{
                marginTop: "var(--space-2)",
                padding: "4px 12px",
                fontSize: "var(--font-size-xs)",
                fontWeight: "bold",
                borderRadius: "var(--radius-xs)",
                border: "1px solid var(--color-teal-border)",
                backgroundColor: "var(--color-teal-light)",
                color: "var(--color-teal)",
                cursor: "pointer",
              }}
            >
              Reset Filters
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
