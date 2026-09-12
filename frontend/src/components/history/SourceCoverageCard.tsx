"use client";

import React, { useState } from "react";
import { SourceCoverage, UnsupportedReason, SourceSubset } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { getVenueInfo } from "@/lib/venues";

export interface SourceCoverageCardProps {
  sources: SourceCoverage[];
  unsupported: UnsupportedReason[];
  selectedFamilies: string[];
  sourceSubset?: SourceSubset;
}

export const SourceCoverageCard: React.FC<SourceCoverageCardProps> = ({
  sources = [],
  unsupported = [],
  selectedFamilies = [],
  sourceSubset,
}) => {
  const [filterMode, setFilterMode] = useState<"all" | "usable" | "unsupported">("usable");

  const subsetSources = Array.isArray(sourceSubset)
    ? sourceSubset
    : sourceSubset && typeof sourceSubset === "object" && Array.isArray(sourceSubset.sources)
    ? sourceSubset.sources
    : null;

  const usableSources = sources.filter((s) => parseInt(s.usable_pools || "0", 10) > 0);
  const unsupportedSources = sources.filter(
    (s) => s.status === "discovered_unsupported" || s.status === "not_deployed_at_block" || parseInt(s.usable_pools || "0", 10) === 0
  );

  const displayedSources =
    filterMode === "usable"
      ? usableSources
      : filterMode === "unsupported"
      ? unsupportedSources
      : sources;

  const totalDiscovered = sources.reduce(
    (acc, s) => acc + parseInt(s.discovered_pools || "0", 10),
    0
  );
  const totalUsable = sources.reduce(
    (acc, s) => acc + parseInt(s.usable_pools || "0", 10),
    0
  );

  return (
    <div
      style={{
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-4)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          borderBottom: "1px solid var(--color-border-subtle)",
          paddingBottom: "var(--space-2)",
        }}
      >
        <div>
          <div
            style={{
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Routing Universe Source Coverage
          </div>
          <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
            Actual evaluated liquidity families at this historical block ({selectedFamilies.length} selected)
            {subsetSources && subsetSources.length > 0 ? ` (Subset: ${subsetSources.join(", ")})` : " (Universe)"}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
          <button
            type="button"
            onClick={() => setFilterMode("usable")}
            style={{
              fontSize: "11px",
              padding: "2px 8px",
              borderRadius: "var(--radius-xs)",
              border: "1px solid",
              borderColor: filterMode === "usable" ? "var(--color-teal)" : "var(--color-border)",
              backgroundColor: filterMode === "usable" ? "var(--color-teal-light)" : "transparent",
              color: filterMode === "usable" ? "var(--color-teal)" : "var(--color-text-secondary)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}
          >
            Usable ({usableSources.length})
          </button>
          <button
            type="button"
            onClick={() => setFilterMode("unsupported")}
            style={{
              fontSize: "11px",
              padding: "2px 8px",
              borderRadius: "var(--radius-xs)",
              border: "1px solid",
              borderColor: filterMode === "unsupported" ? "var(--color-teal)" : "var(--color-border)",
              backgroundColor: filterMode === "unsupported" ? "var(--color-teal-light)" : "transparent",
              color: filterMode === "unsupported" ? "var(--color-teal)" : "var(--color-text-secondary)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}
          >
            Excluded ({unsupportedSources.length})
          </button>
          <button
            type="button"
            onClick={() => setFilterMode("all")}
            style={{
              fontSize: "11px",
              padding: "2px 8px",
              borderRadius: "var(--radius-xs)",
              border: "1px solid",
              borderColor: filterMode === "all" ? "var(--color-teal)" : "var(--color-border)",
              backgroundColor: filterMode === "all" ? "var(--color-teal-light)" : "transparent",
              color: filterMode === "all" ? "var(--color-teal)" : "var(--color-text-secondary)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}
          >
            All ({sources.length})
          </button>
        </div>
      </div>

      {/* High-level Pool Stats */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-4)",
          fontSize: "var(--font-size-xs)",
        }}
      >
        <div>
          <span style={{ color: "var(--color-text-muted)" }}>Total Discovered: </span>
          <span className="font-mono tabular-nums" style={{ fontWeight: "bold" }}>
            {totalDiscovered} pools
          </span>
        </div>
        <div>
          <span style={{ color: "var(--color-text-muted)" }}>Usable In Universe: </span>
          <span className="font-mono tabular-nums" style={{ fontWeight: "bold", color: "var(--color-teal)" }}>
            {totalUsable} pools
          </span>
        </div>
      </div>

      {/* Grid of Sources */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: "var(--space-2)",
        }}
      >
        {displayedSources.map((s) => {
          const info = getVenueInfo(s.family);
          const usable = parseInt(s.usable_pools || "0", 10);
          const discovered = parseInt(s.discovered_pools || "0", 10);
          const reasons = unsupported.filter((u) => u.family === s.family);

          return (
            <div
              key={s.family}
              style={{
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-xs)",
                padding: "var(--space-2)",
                backgroundColor: usable > 0 ? "var(--color-surface)" : "var(--color-surface-muted)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Badge variant="venue" venueFamily={s.family}>
                  {info.name}
                </Badge>
                <span
                  className="font-mono tabular-nums"
                  style={{
                    fontSize: "11px",
                    fontWeight: "bold",
                    color: usable > 0 ? "var(--color-teal)" : "var(--color-text-muted)",
                  }}
                >
                  {usable} / {discovered}
                </span>
              </div>

              <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                {usable > 0 ? (
                  <span style={{ color: "var(--color-success-text)" }}>✓ Active &amp; solvable</span>
                ) : s.status === "unavailable" ? (
                  <span>Off-chain / historical unavailable</span>
                ) : reasons.length > 0 ? (
                  <span title={reasons[0].reason}>
                    {reasons[0].reason.length > 40
                      ? `${reasons[0].reason.slice(0, 38)}...`
                      : reasons[0].reason}
                  </span>
                ) : (
                  <span>Adapter unsupported</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
