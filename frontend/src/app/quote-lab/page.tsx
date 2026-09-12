"use client";

import React, { useEffect, useMemo, useState } from "react";
import { CatalogReportEntry } from "@/lib/types";
import { getPins, getSavedReports, getTokens } from "@/lib/catalog";
import { QuoteForm } from "@/components/quote/QuoteForm";
import { ReportDetail } from "@/components/history/ReportDetail";
import { Badge } from "@/components/ui/Badge";
import { formatRawUnits, shortenHash } from "@/lib/formatting";

export default function QuoteLabPage() {
  const pins = useMemo(() => getPins(), []);
  const tokens = useMemo(() => getTokens(), []);
  const savedReports = useMemo(() => getSavedReports(), []);

  const [adhocReports, setAdhocReports] = useState<CatalogReportEntry[]>([]);
  const [activeReport, setActiveReport] = useState<CatalogReportEntry | null>(null);
  const [backendStatus, setBackendStatus] = useState<"checking" | "online" | "offline">("checking");

  // Check connectivity honestly via /api/health proxy and load cached reports
  useEffect(() => {
    async function checkHealthAndLoadReports() {
      try {
        const healthRes = await fetch("/api/health");
        if (healthRes.ok) {
          setBackendStatus("online");
          // Fetch any stored ad hoc reports
          const reportsRes = await fetch("/api/reports");
          if (reportsRes.ok) {
            const data = await reportsRes.json();
            if (Array.isArray(data.reports) && data.reports.length > 0) {
              setAdhocReports(data.reports);
              setActiveReport(data.reports[0]);
            }
          }
        } else {
          setBackendStatus("offline");
        }
      } catch {
        setBackendStatus("offline");
      }
    }
    checkHealthAndLoadReports();
  }, []);

  const handleQuoteSuccess = (newEntry: CatalogReportEntry) => {
    setAdhocReports((prev) => [newEntry, ...prev.filter((r) => r.id !== newEntry.id)]);
    setActiveReport(newEntry);
  };

  const allAvailableReports = useMemo(() => {
    return [...adhocReports, ...savedReports];
  }, [adhocReports, savedReports]);

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
      {/* Header Banner */}
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
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <h1
              style={{
                fontSize: "var(--font-size-2xl)",
                fontWeight: "var(--font-weight-bold)",
                color: "var(--color-text)",
                letterSpacing: "-0.02em",
                lineHeight: "1.2",
              }}
            >
              Historical Quote Laboratory
            </h1>
            <Badge variant={backendStatus === "online" ? "success" : "neutral"}>
              {backendStatus === "checking"
                ? "Checking Engine..."
                : backendStatus === "online"
                ? "Quote Engine Available"
                : "Quote Engine Unavailable"}
            </Badge>
          </div>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
              marginTop: "var(--space-1)",
              maxWidth: "780px",
            }}
          >
            Execute custom ad hoc routing evaluations against pinned historical state snapshots. Specify tokens, exact input amounts, solver strategy, and target DEX families.
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
          <div>Pinned Anchors: {pins.length}</div>
          <div>Ad Hoc Solves: {adhocReports.length}</div>
        </div>
      </div>

      {/* Main Grid: Form on Left, Results or Status on Right */}
      <div
        className={activeReport ? "quote-grid quote-grid-results" : "quote-grid"}
        style={{
          display: "grid",
          gap: "var(--space-6)",
          alignItems: "start",
        }}
      >
        {/* Left Column: Quote Form and Recent Sessions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <QuoteForm
            tokens={tokens}
            pins={pins}
            onQuoteSuccess={handleQuoteSuccess}
          />

          {/* Recent Ad Hoc Solves */}
          {adhocReports.length > 0 && (
            <div
              style={{
                backgroundColor: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--space-4)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-2)",
              }}
            >
              <div
                style={{
                  fontSize: "var(--font-size-xs)",
                  fontWeight: "var(--font-weight-bold)",
                  color: "var(--color-text-secondary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                Recent Session Quotes ({adhocReports.length})
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                {adhocReports.map((entry) => {
                  const rep = entry.report;
                  const isSelected = activeReport?.id === entry.id;
                  const inAmt = formatRawUnits(rep.request.amount_in, rep.request.decimals_in, 0);
                  const outAmt = rep.best_split
                    ? formatRawUnits(rep.best_split.amount_out, rep.request.decimals_out, 2)
                    : "No route";

                  return (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => setActiveReport(entry)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "6px 10px",
                        fontSize: "var(--font-size-xs)",
                        borderRadius: "var(--radius-xs)",
                        border: `1px solid ${isSelected ? "var(--color-teal)" : "var(--color-border-subtle)"}`,
                        backgroundColor: isSelected ? "var(--color-teal-light)" : "var(--color-surface-muted)",
                        cursor: "pointer",
                        textAlign: "left",
                        transition: "all var(--transition-fast)",
                      }}
                    >
                      <span className="font-mono tabular-nums" style={{ fontWeight: "bold" }}>
                        #{rep.block} · {inAmt} {rep.request.symbol_in} → {outAmt} {rep.request.symbol_out}
                      </span>
                      <span className="font-mono" style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>
                        {shortenHash(entry.id, 4, 4)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Right Column: Active Quote Detail */}
        {activeReport ? (
          <div>
            <ReportDetail
              entry={activeReport}
              allReports={allAvailableReports}
              onSelectReport={(id) => {
                const found = allAvailableReports.find((r) => r.id === id);
                if (found) setActiveReport(found);
              }}
            />
          </div>
        ) : (
          <div
            style={{
              backgroundColor: "var(--color-surface-muted)",
              border: "1px dashed var(--color-border-strong)",
              borderRadius: "var(--radius-sm)",
              padding: "var(--space-8)",
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "var(--space-2)",
            }}
          >
            <div style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--color-text)" }}>
              No Active Quote Selected
            </div>
            <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)", maxWidth: "480px" }}>
              Submit a quote request using the form on the left to compute real multi-pool routing via the local solver engine, or review the pre-computed anchors in the Historical workspace.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
