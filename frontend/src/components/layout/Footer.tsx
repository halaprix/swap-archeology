import React from "react";

export const Footer: React.FC = () => {
  return (
    <footer
      style={{
        borderTop: "1px solid var(--color-border)",
        backgroundColor: "var(--color-surface)",
        padding: "var(--space-6) var(--space-6)",
        marginTop: "auto",
      }}
    >
      <div
        style={{
          maxWidth: "1440px",
          margin: "0 auto",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-4)",
          fontSize: "var(--font-size-xs)",
          color: "var(--color-text-muted)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <span style={{ fontWeight: "var(--font-weight-medium)", color: "var(--color-text-secondary)" }}>
            SWAP ARCHEOLOGY
          </span>
          <span>·</span>
          <span>Historical execution analysis &amp; multi-venue route solver benchmarks</span>
          <span>·</span>
          <span>No live trades or wallet execution</span>
        </div>

        <div
          className="font-mono"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-3)",
            fontSize: "11px",
          }}
        >
          <span>Mainnet Historical Archive</span>
          <span>·</span>
          <span>Catalog Schema v1</span>
        </div>
      </div>
    </footer>
  );
};
