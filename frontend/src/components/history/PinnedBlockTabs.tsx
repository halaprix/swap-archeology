"use client";

import React from "react";
import { CatalogPin } from "@/lib/types";
import { formatTimestamp, shortenHash } from "@/lib/formatting";

export interface PinnedBlockTabsProps {
  pins: CatalogPin[];
  selectedBlock: string;
  onSelectPin: (blockNumber: string) => void;
}

export const PinnedBlockTabs: React.FC<PinnedBlockTabsProps> = ({
  pins,
  selectedBlock,
  onSelectPin,
}) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
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
          Pinned Historical Blocks (5 Pins)
        </span>
        <span
          style={{
            fontSize: "11px",
            color: "var(--color-text-muted)",
          }}
        >
          Archive State Snapshot Anchors
        </span>
      </div>

      <div
        role="tablist"
        aria-label="Pinned Historical Blocks"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: "var(--space-2)",
        }}
      >
        {pins.map((pin) => {
          const isSelected = selectedBlock === pin.number;
          return (
            <button
              key={pin.number}
              role="tab"
              aria-selected={isSelected}
              onClick={() => onSelectPin(pin.number)}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-start",
                gap: "2px",
                padding: "var(--space-2) var(--space-3)",
                backgroundColor: isSelected ? "var(--color-teal-light)" : "var(--color-surface)",
                border: `1px solid ${isSelected ? "var(--color-teal-border)" : "var(--color-border)"}`,
                borderRadius: "var(--radius-sm)",
                boxShadow: isSelected ? "var(--shadow-sm)" : "none",
                cursor: "pointer",
                textAlign: "left",
                transition: "all var(--transition-fast)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  width: "100%",
                }}
              >
                <span
                  className="font-mono tabular-nums"
                  style={{
                    fontSize: "var(--font-size-sm)",
                    fontWeight: isSelected ? "var(--font-weight-bold)" : "var(--font-weight-semibold)",
                    color: isSelected ? "var(--color-teal)" : "var(--color-text)",
                  }}
                >
                  #{pin.number}
                </span>
                {isSelected && (
                  <span
                    style={{
                      width: "6px",
                      height: "6px",
                      borderRadius: "50%",
                      backgroundColor: "var(--color-teal)",
                    }}
                    aria-hidden="true"
                  />
                )}
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: isSelected ? "var(--color-teal-hover)" : "var(--color-text-muted)",
                  fontWeight: "var(--font-weight-medium)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  maxWidth: "100%",
                }}
              >
                {pin.label || shortenHash(pin.hash, 8, 4)}
              </div>
              <div
                className="font-mono"
                style={{
                  fontSize: "10px",
                  color: "var(--color-text-muted)",
                  marginTop: "1px",
                }}
              >
                {formatTimestamp(pin.timestamp).slice(0, 10)}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};
