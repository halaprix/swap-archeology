"use client";

import React, { useState } from "react";
import { CatalogReportEntry, SwapReport } from "@/lib/types";
import { Metric } from "@/components/ui/Metric";
import { Badge } from "@/components/ui/Badge";
import { calculateBaselineGain, formatRawUnits, formatTimestamp, shortenHash } from "@/lib/formatting";
import { RouteFlow } from "./RouteFlow";
import { RouteStepTable } from "./RouteStepTable";
import { SourceCoverageCard } from "./SourceCoverageCard";
import { HistoricalTrend } from "./HistoricalTrend";

export interface ReportDetailProps {
  entry: CatalogReportEntry;
  allReports: CatalogReportEntry[];
  onSelectReport: (id: string) => void;
}

export const ReportDetail: React.FC<ReportDetailProps> = ({
  entry,
  allReports,
  onSelectReport,
}) => {
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(0);
  const [viewMode, setViewMode] = useState<"flow" | "table" | "both" | "json">("both");

  const report: SwapReport = entry.report;
  const req = report.request;

  const decimalsIn = parseInt(req.decimals_in, 10);
  const decimalsOut = parseInt(req.decimals_out, 10);

  const formattedIn = formatRawUnits(req.amount_in, decimalsIn, 4);
  const formattedOut = report.best_split
    ? formatRawUnits(report.best_split.amount_out, decimalsOut, 4)
    : null;

  const baselineOut = report.single_pool_baseline?.amount_out;
  const formattedBaseline = baselineOut
    ? formatRawUnits(baselineOut, decimalsOut, 4)
    : null;

  const gain = calculateBaselineGain(baselineOut, report.best_split?.amount_out, decimalsOut);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-6)",
      }}
    >
      {/* Header Context Bar */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "var(--space-3)",
          borderBottom: "1px solid var(--color-border)",
          paddingBottom: "var(--space-4)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <h2
              style={{
                fontSize: "var(--font-size-xl)",
                fontWeight: "var(--font-weight-bold)",
                color: "var(--color-text)",
                letterSpacing: "-0.01em",
              }}
            >
              Block #{report.block}
            </h2>
            <Badge variant={entry.origin === "adhoc" ? "warning" : "success"}>
              {entry.origin === "adhoc" ? "Ad Hoc Snapshot" : "Pinned Archive Snapshot"}
            </Badge>
            <Badge variant="info">
              {report.requested_solver === "baseline"
                ? "BASELINE SOLVER (BOUNDED TWO-HOP)"
                : report.requested_solver === "search"
                ? "SEARCH SOLVER (MULTI-SPLIT)"
                : report.requested_solver === "dual"
                ? "DUAL SOLVER (LP BOUND)"
                : `${report.requested_solver.toUpperCase()} SOLVER`}
            </Badge>
          </div>
          <div
            className="font-mono"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-muted)",
              marginTop: "4px",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              flexWrap: "wrap",
            }}
          >
            <span>Hash: {report.block_hash}</span>
            <span>·</span>
            <span>Time: {formatTimestamp(report.timestamp)}</span>
            <span>·</span>
            <span>Report ID: {shortenHash(entry.id, 8, 6)}</span>
          </div>
        </div>

        {/* View mode toggle */}
        <div
          style={{
            display: "inline-flex",
            backgroundColor: "var(--color-surface-muted)",
            padding: "2px",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--color-border)",
          }}
        >
          {(["both", "flow", "table", "json"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setViewMode(mode)}
              style={{
                padding: "3px 10px",
                fontSize: "var(--font-size-xs)",
                fontFamily: "var(--font-mono)",
                fontWeight: viewMode === mode ? "bold" : "normal",
                color: viewMode === mode ? "var(--color-text)" : "var(--color-text-secondary)",
                backgroundColor: viewMode === mode ? "var(--color-surface)" : "transparent",
                border: "none",
                borderRadius: "var(--radius-xs)",
                boxShadow: viewMode === mode ? "var(--shadow-sm)" : "none",
                cursor: "pointer",
                textTransform: "capitalize",
              }}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      {/* Top Large Tabular Metrics Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        <Metric
          label="Input Amount"
          value={formattedIn}
          unit={req.symbol_in}
          rawExact={req.amount_in}
          subtext={`Decimals: ${req.decimals_in}`}
        />

        {report.best_split ? (
          <Metric
            label="Best Split Output"
            value={formattedOut}
            unit={req.symbol_out}
            rawExact={report.best_split.amount_out}
            variant="highlight"
            badge={{
              text: `${report.best_split.steps.length} Pool Steps`,
              variant: "success",
            }}
            subtext={
              report.best_split.search_info.kind
                ? `Strategy: ${report.best_split.search_info.kind}`
                : undefined
            }
          />
        ) : (
          <Metric
            label="Best Split Output"
            value="No Route Found"
            variant="muted"
            badge={{
              text: "Empty Result",
              variant: "warning",
            }}
            subtext="Solver could not construct an executable route within bounds"
          />
        )}

        <Metric
          label="Single-Pool Baseline"
          value={formattedBaseline || "—"}
          unit={req.symbol_out}
          rawExact={baselineOut || undefined}
          subtext="Direct single-pool comparison metric"
        />

        {gain ? (
          <Metric
            label="Baseline Improvement"
            value={gain.percentGain}
            unit=""
            variant={gain.isZero ? "default" : gain.isGain ? "highlight" : "muted"}
            badge={{
              text: gain.isZero
                ? "Matches baseline"
                : gain.isGain
                ? "Split Outperforms"
                : "Baseline Better",
              variant: gain.isZero ? "neutral" : gain.isGain ? "success" : "warning",
            }}
            subtext={
              gain.isZero
                ? "Equivalent to single-pool baseline candidate"
                : `Absolute difference: ${gain.formattedDelta} ${req.symbol_out}`
            }
            rawExact={`delta: ${gain.absoluteDeltaRaw}`}
          />
        ) : (
          <Metric
            label="Baseline Improvement"
            value="—"
            subtext="Calculated when baseline and route both exist"
          />
        )}

        <Metric
          label="Execution Gas Estimate"
          value={
            report.best_split?.gas_estimate
              ? `${parseInt(report.best_split.gas_estimate, 10).toLocaleString()} gas`
              : "—"
          }
          subtext="Model adapter estimate"
          rawExact={report.best_split?.gas_estimate || undefined}
        />
      </div>

      {/* Main Routing Flow Visualization and Table */}
      {viewMode === "json" ? (
        <div
          style={{
            backgroundColor: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            padding: "var(--space-4)",
            overflowX: "auto",
          }}
        >
          <pre
            className="font-mono"
            style={{ fontSize: "11px", color: "var(--color-text)", lineHeight: "1.5" }}
          >
            {JSON.stringify(report, null, 2)}
          </pre>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
          {(viewMode === "both" || viewMode === "flow") && (
            <div>
              <div
                style={{
                  fontSize: "var(--font-size-xs)",
                  fontWeight: "var(--font-weight-bold)",
                  color: "var(--color-text)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: "var(--space-2)",
                }}
              >
                Route
              </div>
              <RouteFlow
                steps={report.best_split?.steps || []}
                report={report}
                selectedStepIndex={selectedStepIndex}
                onSelectStep={setSelectedStepIndex}
              />
            </div>
          )}

          {(viewMode === "both" || viewMode === "table") && (
            <div
              style={{
                backgroundColor: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--space-4)",
              }}
            >
              <RouteStepTable
                steps={report.best_split?.steps || []}
                report={report}
                selectedStepIndex={selectedStepIndex}
                onSelectStep={setSelectedStepIndex}
              />
            </div>
          )}
        </div>
      )}

      {/* Historical Trend Timeline (Strictly identical observations) */}
      <HistoricalTrend
        currentReport={report}
        allReports={allReports}
        onSelectReport={onSelectReport}
      />

      {/* Source Coverage & Protocol Availability Breakdown */}
      <SourceCoverageCard
        sources={report.sources || []}
        unsupported={report.unsupported || []}
        selectedFamilies={report.selected_families || []}
        sourceSubset={report.source_subset}
      />

      {/* Audit Limitations & Formal Disclaimers */}
      {report.limitations && report.limitations.length > 0 && (
        <div
          style={{
            backgroundColor: "var(--color-surface-muted)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            padding: "var(--space-3) var(--space-4)",
            fontSize: "var(--font-size-xs)",
            color: "var(--color-text-secondary)",
          }}
        >
          <div
            style={{
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
              textTransform: "uppercase",
              fontSize: "11px",
              letterSpacing: "0.05em",
              marginBottom: "var(--space-1)",
            }}
          >
            Research Bounds &amp; Limitations
          </div>
          <ul style={{ paddingLeft: "var(--space-4)", margin: 0, display: "flex", flexDirection: "column", gap: "2px" }}>
            {report.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
