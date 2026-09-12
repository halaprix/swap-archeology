"use client";

import React, { useMemo } from "react";
import { RouteStep, SwapReport } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { parsePoolId, shortenHash } from "@/lib/formatting";
import { getTokens } from "@/lib/catalog";
import { formatTokenAmount, resolveTokenMetadata } from "@/lib/tokenMetadata";

export interface RouteStepTableProps {
  steps: RouteStep[];
  report: SwapReport;
  selectedStepIndex: number | null;
  onSelectStep: (index: number) => void;
}

export const RouteStepTable: React.FC<RouteStepTableProps> = ({
  steps,
  report,
  selectedStepIndex,
  onSelectStep,
}) => {
  const catalogTokens = useMemo(() => getTokens(), []);

  if (!steps || steps.length === 0) {
    return (
      <div
        style={{
          padding: "var(--space-4)",
          textAlign: "center",
          color: "var(--color-text-muted)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        No route execution steps recorded.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto", width: "100%" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: "var(--font-size-xs)",
          textAlign: "left",
        }}
      >
        <caption
          style={{
            textAlign: "left",
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-semibold)",
            color: "var(--color-text-secondary)",
            paddingBottom: "var(--space-2)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Ordered Execution Step Ledger ({steps.length} Steps)
        </caption>
        <thead>
          <tr
            style={{
              backgroundColor: "var(--color-surface-muted)",
              borderBottom: "1px solid var(--color-border-strong)",
              borderTop: "1px solid var(--color-border)",
            }}
          >
            <th scope="col" style={{ padding: "8px 10px", width: "45px" }}>
              #
            </th>
            <th scope="col" style={{ padding: "8px 10px" }}>
              Venue / Protocol
            </th>
            <th scope="col" style={{ padding: "8px 10px" }}>
              Pool Identifier
            </th>
            <th scope="col" style={{ padding: "8px 10px" }}>
              Input (Token &amp; Amount)
            </th>
            <th scope="col" style={{ padding: "8px 10px" }}>
              Output (Token &amp; Amount)
            </th>
            <th scope="col" style={{ padding: "8px 10px", textAlign: "right" }}>
              Exact Raw Integers
            </th>
          </tr>
        </thead>
        <tbody>
          {steps.map((step, idx) => {
            const isSelected = selectedStepIndex === idx;
            const parsed = parsePoolId(step.pool_id);

            const inMeta = resolveTokenMetadata(step.token_in, catalogTokens, report.request);
            const outMeta = resolveTokenMetadata(step.token_out, catalogTokens, report.request);

            const formattedIn = formatTokenAmount(step.amount_in, inMeta, 4);
            const formattedOut = formatTokenAmount(step.amount_out, outMeta, 4);

            return (
              <tr
                key={`${step.pool_id}-${idx}`}
                onClick={() => onSelectStep(idx)}
                tabIndex={0}
                role="button"
                aria-pressed={isSelected}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectStep(idx);
                  }
                }}
                style={{
                  backgroundColor: isSelected ? "var(--color-teal-light)" : "var(--color-surface)",
                  borderBottom: "1px solid var(--color-border)",
                  cursor: "pointer",
                  transition: "background-color var(--transition-fast)",
                }}
              >
                <td
                  className="font-mono tabular-nums"
                  style={{
                    padding: "8px 10px",
                    fontWeight: isSelected ? "var(--font-weight-bold)" : "var(--font-weight-medium)",
                    color: isSelected ? "var(--color-teal)" : "var(--color-text-secondary)",
                  }}
                >
                  {idx + 1}
                </td>
                <td style={{ padding: "8px 10px" }}>
                  <Badge variant="venue" venueFamily={parsed.family}>
                    {parsed.family.replace(/_/g, " ")}
                  </Badge>
                </td>
                <td
                  className="font-mono"
                  style={{
                    padding: "8px 10px",
                    color: "var(--color-text)",
                  }}
                  title={step.pool_id}
                >
                  <span>{shortenHash(parsed.address, 6, 4)}</span>
                </td>
                <td style={{ padding: "8px 10px" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-1)" }}>
                    <span className="font-mono tabular-nums" style={{ fontWeight: "var(--font-weight-semibold)" }}>
                      {formattedIn}
                    </span>
                    <span style={{ color: "var(--color-text-secondary)", fontWeight: "var(--font-weight-medium)" }}>
                      {inMeta.symbol}
                    </span>
                  </div>
                </td>
                <td style={{ padding: "8px 10px" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-1)" }}>
                    <span
                      className="font-mono tabular-nums"
                      style={{
                        fontWeight: "var(--font-weight-semibold)",
                        color: isSelected ? "var(--color-teal)" : "var(--color-text)",
                      }}
                    >
                      {formattedOut}
                    </span>
                    <span style={{ color: "var(--color-text-secondary)", fontWeight: "var(--font-weight-medium)" }}>
                      {outMeta.symbol}
                    </span>
                  </div>
                </td>
                <td
                  className="font-mono tabular-nums"
                  style={{
                    padding: "8px 10px",
                    textAlign: "right",
                    color: "var(--color-text-muted)",
                    fontSize: "11px",
                  }}
                >
                  <div>in: {step.amount_in}</div>
                  <div>out: {step.amount_out}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
