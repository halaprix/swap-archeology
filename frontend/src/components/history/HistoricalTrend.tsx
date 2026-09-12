"use client";

import React from "react";
import { CatalogReportEntry, SwapReport } from "@/lib/types";
import { calculateBaselineGain, formatRawUnits, formatTimestamp } from "@/lib/formatting";
import { findIdenticalObservations } from "@/lib/catalog";

export interface HistoricalTrendProps {
  currentReport: SwapReport;
  allReports: CatalogReportEntry[];
  onSelectReport: (id: string) => void;
}

export const HistoricalTrend: React.FC<HistoricalTrendProps> = ({
  currentReport,
  allReports,
  onSelectReport,
}) => {
  const observations = findIdenticalObservations(currentReport, allReports);
  const req = currentReport.request;
  const decimalsOut = parseInt(req.decimals_out, 10);

  if (observations.length <= 1) {
    return (
      <div
        style={{
          padding: "var(--space-4)",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-sm)",
          fontSize: "var(--font-size-xs)",
          color: "var(--color-text-secondary)",
        }}
      >
        <div style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--color-text)" }}>
          Historical Trend: Single Observation
        </div>
        <p style={{ marginTop: "4px", color: "var(--color-text-muted)" }}>
          Only 1 saved snapshot matches this exact pair ({req.symbol_in} → {req.symbol_out}), raw amount ({req.amount_in}), solver ({currentReport.requested_solver}), and source universe. Historical trends strictly plot identical evaluated configurations without price interpolation.
        </p>
      </div>
    );
  }

  // Calculate coordinates for discrete SVG plot
  // Note: best_split=null represents a missing/unfeasible route, NEVER zero-output.
  const outputs = observations.map((obs) => {
    const splitOut = obs.report.best_split?.amount_out || null;
    const baseOut = obs.report.single_pool_baseline?.amount_out || null;

    return {
      id: obs.id,
      block: obs.report.block,
      timestamp: obs.report.timestamp,
      splitOutRaw: splitOut,
      baseOutRaw: baseOut,
      // Only scale positive BigInt values for Y coordinates; null means no route
      splitOutNum: splitOut ? Number(BigInt(splitOut) / BigInt(1000000)) : null,
      baseOutNum: baseOut ? Number(BigInt(baseOut) / BigInt(1000000)) : null,
      gain: splitOut && baseOut ? calculateBaselineGain(baseOut, splitOut, decimalsOut) : null,
      isActive: obs.id === currentReport.block_hash || obs.report.block === currentReport.block && obs.report.requested_solver === currentReport.requested_solver,
    };
  });

  // Calculate domain excluding nulls (do NOT include zero in chart domain)
  const validOutputs: number[] = [];
  outputs.forEach((o) => {
    if (o.splitOutNum !== null && !isNaN(o.splitOutNum)) validOutputs.push(o.splitOutNum);
    if (o.baseOutNum !== null && !isNaN(o.baseOutNum)) validOutputs.push(o.baseOutNum);
  });

  const minVal = validOutputs.length > 0 ? Math.min(...validOutputs) : 100;
  const maxVal = validOutputs.length > 0 ? Math.max(...validOutputs) : 200;
  const valRange = maxVal - minVal > 0 ? maxVal - minVal : 1;

  const chartWidth = 720;
  const chartHeight = 170;
  const padX = 60;
  const padY = 32;

  const getX = (idx: number) =>
    padX + (idx / Math.max(1, outputs.length - 1)) * (chartWidth - padX * 2);

  const getY = (val: number | null) => {
    if (val === null) return null;
    return chartHeight - padY - ((val - minVal) / valRange) * (chartHeight - padY * 2);
  };

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
            Historical Observation Sequence ({observations.length} Discrete Records)
          </div>
          <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
            Exact saved solver outputs for identical {formatRawUnits(req.amount_in, req.decimals_in, 0)} {req.symbol_in} → {req.symbol_out}. Missing routes shown as gaps; no synthetic interpolation.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-3)",
            fontSize: "11px",
            fontFamily: "var(--font-mono)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: "var(--color-teal)",
                display: "inline-block",
              }}
            />
            <span>Best Split Output</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: "var(--color-border-strong)",
                display: "inline-block",
              }}
            />
            <span>Single-Pool Baseline</span>
          </div>
        </div>
      </div>

      {/* Discrete SVG Plot */}
      <div style={{ overflowX: "auto" }}>
        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          width="100%"
          height={chartHeight}
          style={{ minWidth: "560px", display: "block" }}
          role="img"
          aria-label="Historical observation chart comparing baseline and best split outputs"
        >
          {/* Baseline discrete step lines */}
          {outputs.map((o, idx) => {
            if (idx === 0) return null;
            const prev = outputs[idx - 1];
            const y1 = getY(prev.baseOutNum);
            const y2 = getY(o.baseOutNum);
            if (y1 === null || y2 === null) return null;
            return (
              <line
                key={`base-line-${idx}`}
                x1={getX(idx - 1)}
                y1={y1}
                x2={getX(idx)}
                y2={y2}
                stroke="var(--color-border-strong)"
                strokeWidth="1.5"
                strokeDasharray="4 4"
              />
            );
          })}

          {/* Best split discrete step lines (only between valid routes) */}
          {outputs.map((o, idx) => {
            if (idx === 0) return null;
            const prev = outputs[idx - 1];
            const y1 = getY(prev.splitOutNum);
            const y2 = getY(o.splitOutNum);
            if (y1 === null || y2 === null) return null;
            return (
              <line
                key={`split-line-${idx}`}
                x1={getX(idx - 1)}
                y1={y1}
                x2={getX(idx)}
                y2={y2}
                stroke="var(--color-teal)"
                strokeWidth="2"
              />
            );
          })}

          {/* Observation Points */}
          {outputs.map((o, idx) => {
            const x = getX(idx);
            const ySplit = getY(o.splitOutNum);
            const yBase = getY(o.baseOutNum);

            return (
              <g
                key={o.id}
                onClick={() => onSelectReport(o.id)}
                style={{ cursor: "pointer" }}
                role="button"
                tabIndex={0}
                aria-label={`Block ${o.block}: Output ${o.splitOutRaw ? formatRawUnits(o.splitOutRaw, decimalsOut, 2) : "No Route"} ${req.symbol_out}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectReport(o.id);
                  }
                }}
              >
                {/* Vertical block guide */}
                <line
                  x1={x}
                  y1={padY}
                  x2={x}
                  y2={chartHeight - padY}
                  stroke={o.isActive ? "var(--color-teal-border)" : "var(--color-border-subtle)"}
                  strokeWidth={o.isActive ? 2 : 1}
                />

                {/* Baseline Dot */}
                {yBase !== null && (
                  <circle cx={x} cy={yBase} r={3} fill="var(--color-border-strong)" />
                )}

                {/* Split Dot or No-Route Marker */}
                {ySplit !== null ? (
                  <>
                    <circle
                      cx={x}
                      cy={ySplit}
                      r={o.isActive ? 6 : 4}
                      fill="var(--color-teal)"
                      stroke="var(--color-surface)"
                      strokeWidth="2"
                    />
                    <text
                      x={x}
                      y={ySplit - 10}
                      textAnchor="middle"
                      fontSize="10"
                      fontFamily="var(--font-mono)"
                      fontWeight="bold"
                      fill={o.isActive ? "var(--color-teal)" : "var(--color-text)"}
                    >
                      {formatRawUnits(o.splitOutRaw, decimalsOut, 0)}
                    </text>
                  </>
                ) : (
                  <>
                    {/* Explicit No Route gap indicator */}
                    <circle
                      cx={x}
                      cy={padY + 20}
                      r={5}
                      fill="var(--color-surface-muted)"
                      stroke="var(--color-warning)"
                      strokeWidth="2"
                    />
                    <text
                      x={x}
                      y={padY + 12}
                      textAnchor="middle"
                      fontSize="9"
                      fontFamily="var(--font-mono)"
                      fontWeight="bold"
                      fill="var(--color-warning-text)"
                    >
                      No Route
                    </text>
                  </>
                )}

                {/* Block Number Label */}
                <text
                  x={x}
                  y={chartHeight - 10}
                  textAnchor="middle"
                  fontSize="10"
                  fontFamily="var(--font-mono)"
                  fill={o.isActive ? "var(--color-teal)" : "var(--color-text-muted)"}
                  fontWeight={o.isActive ? "bold" : "normal"}
                >
                  #{o.block}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Discrete Table of Observations */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "var(--space-2)",
          fontSize: "11px",
        }}
      >
        {outputs.map((o) => (
          <button
            key={`tab-obs-${o.id}`}
            type="button"
            onClick={() => onSelectReport(o.id)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              padding: "var(--space-2)",
              borderRadius: "var(--radius-xs)",
              border: `1px solid ${o.isActive ? "var(--color-teal)" : "var(--color-border)"}`,
              backgroundColor: o.isActive ? "var(--color-teal-light)" : "var(--color-surface-muted)",
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <div
              className="font-mono tabular-nums"
              style={{
                fontWeight: o.isActive ? "bold" : "semibold",
                color: o.isActive ? "var(--color-teal)" : "var(--color-text)",
              }}
            >
              #{o.block}
            </div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "10px" }}>
              {formatTimestamp(o.timestamp).slice(0, 10)}
            </div>

            {o.splitOutRaw ? (
              <>
                <div
                  className="font-mono tabular-nums"
                  style={{
                    marginTop: "2px",
                    fontWeight: "bold",
                    color: "var(--color-text)",
                  }}
                >
                  {formatRawUnits(o.splitOutRaw, decimalsOut, 0)} {req.symbol_out}
                </div>
                {o.gain && (
                  <div
                    className="font-mono tabular-nums"
                    style={{
                      fontSize: "10px",
                      color: o.gain.isZero
                        ? "var(--color-text-secondary)"
                        : o.gain.isGain
                        ? "var(--color-success-text)"
                        : "var(--color-warning-text)",
                    }}
                  >
                    {o.gain.isZero ? "Matches baseline" : `${o.gain.percentGain} vs baseline`}
                  </div>
                )}
              </>
            ) : (
              <div
                style={{
                  marginTop: "2px",
                  fontWeight: "bold",
                  color: "var(--color-warning-text)",
                }}
              >
                No route
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
};
