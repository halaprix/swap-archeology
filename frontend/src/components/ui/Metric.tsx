import React from "react";
import { Badge } from "./Badge";

export interface MetricProps {
  label: string;
  value: React.ReactNode;
  unit?: string;
  subtext?: React.ReactNode;
  rawExact?: string;
  badge?: {
    text: string;
    variant: "neutral" | "success" | "warning" | "error" | "info";
  };
  variant?: "default" | "highlight" | "muted";
  className?: string;
  style?: React.CSSProperties;
}

export const Metric: React.FC<MetricProps> = ({
  label,
  value,
  unit,
  subtext,
  rawExact,
  badge,
  variant = "default",
  className = "",
  style,
}) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        padding: "var(--space-3)",
        backgroundColor:
          variant === "highlight"
            ? "var(--color-teal-light)"
            : variant === "muted"
            ? "var(--color-surface-muted)"
            : "var(--color-surface)",
        border: `1px solid ${
          variant === "highlight" ? "var(--color-teal-border)" : "var(--color-border)"
        }`,
        borderRadius: "var(--radius-sm)",
        minWidth: "160px",
        ...style,
      }}
      className={className}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-2)",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-semibold)",
            color: "var(--color-text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          {label}
        </span>
        {badge && <Badge variant={badge.variant}>{badge.text}</Badge>}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-1-5)",
          marginTop: "2px",
        }}
      >
        <span
          className="tabular-nums font-mono"
          style={{
            fontSize: "var(--font-size-xl)",
            fontWeight: "var(--font-weight-bold)",
            color: variant === "highlight" ? "var(--color-teal)" : "var(--color-text)",
            letterSpacing: "-0.02em",
            lineHeight: "1.2",
            wordBreak: "break-all",
          }}
        >
          {value}
        </span>
        {unit && (
          <span
            style={{
              fontSize: "var(--font-size-sm)",
              fontWeight: "var(--font-weight-medium)",
              color: "var(--color-text-secondary)",
            }}
          >
            {unit}
          </span>
        )}
      </div>

      {subtext && (
        <div
          style={{
            fontSize: "var(--font-size-xs)",
            color: "var(--color-text-muted)",
            marginTop: "2px",
          }}
        >
          {subtext}
        </div>
      )}

      {rawExact && (
        <div
          title={`Raw integer: ${rawExact}`}
          className="tabular-nums font-mono"
          style={{
            fontSize: "11px",
            color: "var(--color-text-muted)",
            marginTop: "1px",
            wordBreak: "break-all",
            opacity: 0.85,
          }}
        >
          raw: {rawExact}
        </div>
      )}
    </div>
  );
};
