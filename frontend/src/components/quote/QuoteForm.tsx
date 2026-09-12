"use client";

import React, { useState } from "react";
import { CatalogPin, CatalogReportEntry, CatalogToken } from "@/lib/types";
import { ALL_SOURCE_FAMILIES, getVenueInfo } from "@/lib/venues";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";

export interface QuoteFormProps {
  tokens: CatalogToken[];
  pins: CatalogPin[];
  onQuoteSuccess: (entry: CatalogReportEntry) => void;
}

export const QuoteForm: React.FC<QuoteFormProps> = ({
  tokens,
  pins,
  onQuoteSuccess,
}) => {
  const defaultPin = pins[pins.length - 1]?.number || "25896003";

  const [block, setBlock] = useState(defaultPin);
  const [tokenIn, setTokenIn] = useState(tokens[0]?.symbol || "WETH");
  const [tokenOut, setTokenOut] = useState(tokens[1]?.symbol || "USDC");
  const [amount, setAmount] = useState("100");
  const [solver, setSolver] = useState<"baseline" | "search">("search");
  const [selectedSources, setSelectedSources] = useState<string[]>(ALL_SOURCE_FAMILIES);
  const [showAdvancedSources, setShowAdvancedSources] = useState(false);

  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<{ code: string; message: string } | null>(null);

  const handleToggleSource = (family: string) => {
    setSelectedSources((prev) =>
      prev.includes(family) ? prev.filter((f) => f !== family) : [...prev, family]
    );
  };

  const handleSelectAllSources = () => setSelectedSources(ALL_SOURCE_FAMILIES);
  const handleDeselectAllSources = () => setSelectedSources([]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMessage(null);

    // If all sources are selected, omit sources field to let solver evaluate all families
    const sourcesPayload =
      selectedSources.length === ALL_SOURCE_FAMILIES.length
        ? undefined
        : selectedSources;

    try {
      const res = await fetch("/api/quote", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          block: block.trim(),
          tokenIn: tokenIn.trim(),
          tokenOut: tokenOut.trim(),
          amount: amount.trim(),
          solver,
          sources: sourcesPayload,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        setErrorMessage({
          code: data?.error?.code || "request_failed",
          message:
            data?.error?.message ||
            "Failed to execute quote. Please check your inputs and backend connection.",
        });
        setIsLoading(false);
        return;
      }

      onQuoteSuccess(data as CatalogReportEntry);
    } catch {
      setErrorMessage({
        code: "network_error",
        message: "Failed to connect to /api/quote endpoint.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-4)",
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-5)",
      }}
    >
      <div style={{ borderBottom: "1px solid var(--color-border-subtle)", paddingBottom: "var(--space-3)" }}>
        <h3
          style={{
            fontSize: "var(--font-size-md)",
            fontWeight: "var(--font-weight-bold)",
            color: "var(--color-text)",
            letterSpacing: "-0.01em",
          }}
        >
          Pinned-Block Historical Quote Solver
        </h3>
        <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "2px" }}>
          Compare routes using available liquidity at an exact historical block.
        </p>
      </div>

      {/* Block Anchor Selector */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        <Input
          label="Block Number or 32-Byte Hash"
          placeholder="e.g. 25896003 or 0xf2c9645..."
          value={block}
          onChange={(e) => setBlock(e.target.value)}
          isMono
          required
        />
        {/* Quick Pin Buttons */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", flexWrap: "wrap", marginTop: "2px" }}>
          <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>Pinned Anchors:</span>
          {pins.map((pin) => (
            <button
              key={pin.number}
              type="button"
              onClick={() => setBlock(pin.number)}
              style={{
                fontSize: "11px",
                fontFamily: "var(--font-mono)",
                padding: "1px 6px",
                borderRadius: "var(--radius-xs)",
                border: "1px solid var(--color-border)",
                backgroundColor: block === pin.number ? "var(--color-teal-light)" : "var(--color-surface-muted)",
                color: block === pin.number ? "var(--color-teal)" : "var(--color-text-secondary)",
                cursor: "pointer",
              }}
            >
              #{pin.number}
            </button>
          ))}
        </div>
      </div>

      {/* Token Pair and Amount */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        <Select
          label="Token In"
          value={tokenIn}
          onChange={(e) => setTokenIn(e.target.value)}
          options={tokens.map((t) => ({ value: t.symbol, label: `${t.symbol} (${t.decimals} dec)` }))}
        />

        <Select
          label="Token Out"
          value={tokenOut}
          onChange={(e) => setTokenOut(e.target.value)}
          options={tokens.map((t) => ({ value: t.symbol, label: `${t.symbol} (${t.decimals} dec)` }))}
        />

        <Input
          label="Input Amount"
          type="text"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          isMono
          placeholder="e.g. 100"
          helperText={`Decimal units of ${tokenIn}`}
          required
        />
      </div>

      {/* Solver Choice */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        <span
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-medium)",
            color: "var(--color-text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Solver Strategy
        </span>
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="button"
            onClick={() => setSolver("search")}
            style={{
              flex: 1,
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-sm)",
              border: `1px solid ${solver === "search" ? "var(--color-teal)" : "var(--color-border)"}`,
              backgroundColor: solver === "search" ? "var(--color-teal-light)" : "var(--color-surface)",
              color: solver === "search" ? "var(--color-teal)" : "var(--color-text)",
              textAlign: "left",
              cursor: "pointer",
            }}
          >
            <div style={{ fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-sm)" }}>
              Bounded Search (Split / Multi-Hop)
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
              Explores multi-split allocations and intermediate hops across all selected families
            </div>
          </button>

          <button
            type="button"
            onClick={() => setSolver("baseline")}
            style={{
              flex: 1,
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-sm)",
              border: `1px solid ${solver === "baseline" ? "var(--color-teal)" : "var(--color-border)"}`,
              backgroundColor: solver === "baseline" ? "var(--color-teal-light)" : "var(--color-surface)",
              color: solver === "baseline" ? "var(--color-teal)" : "var(--color-text)",
              textAlign: "left",
              cursor: "pointer",
            }}
          >
            <div style={{ fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-sm)" }}>
              Baseline (Bounded Two-Hop)
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
              Bounded two-hop/two-path routing solver; benchmarks standard multi-pool baseline liquidity
            </div>
          </button>
        </div>
      </div>

      {/* Advanced Source Multi-Select Disclosure */}
      <div
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-xs)",
          padding: "var(--space-3)",
          backgroundColor: "var(--color-surface-muted)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            cursor: "pointer",
          }}
          onClick={() => setShowAdvancedSources(!showAdvancedSources)}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <span style={{ fontSize: "var(--font-size-xs)", fontWeight: "var(--font-weight-bold)", color: "var(--color-text)" }}>
              Source Universe Inclusion ({selectedSources.length} of {ALL_SOURCE_FAMILIES.length} Families)
            </span>
            <Badge variant="neutral">Solver-Level</Badge>
          </div>
          <span style={{ fontSize: "11px", color: "var(--color-teal)", fontWeight: "bold" }}>
            {showAdvancedSources ? "Collapse ▲" : "Configure Sources ▼"}
          </span>
        </div>

        {showAdvancedSources && (
          <div style={{ marginTop: "var(--space-3)", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
              <button
                type="button"
                onClick={handleSelectAllSources}
                style={{
                  fontSize: "11px",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-xs)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-surface)",
                  cursor: "pointer",
                }}
              >
                Select All
              </button>
              <button
                type="button"
                onClick={handleDeselectAllSources}
                style={{
                  fontSize: "11px",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-xs)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-surface)",
                  cursor: "pointer",
                }}
              >
                Clear All
              </button>
              <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                Note: Selection triggers backend recomputation, not edge-hiding.
              </span>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                gap: "var(--space-1)",
                marginTop: "var(--space-2)",
              }}
            >
              {ALL_SOURCE_FAMILIES.map((fam) => {
                const info = getVenueInfo(fam);
                const isChecked = selectedSources.includes(fam);

                return (
                  <label
                    key={fam}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-2)",
                      fontSize: "var(--font-size-xs)",
                      padding: "4px 8px",
                      borderRadius: "var(--radius-xs)",
                      backgroundColor: isChecked ? "var(--color-surface)" : "transparent",
                      border: `1px solid ${isChecked ? "var(--color-border-strong)" : "transparent"}`,
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => handleToggleSource(fam)}
                      style={{ accentColor: "var(--color-teal)" }}
                    />
                    <span>{info.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Error Banner */}
      {errorMessage && (
        <div
          role="alert"
          style={{
            padding: "var(--space-3) var(--space-4)",
            backgroundColor: "var(--color-error-light)",
            border: "1px solid var(--color-error-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--color-error-text)",
            fontSize: "var(--font-size-xs)",
            display: "flex",
            flexDirection: "column",
            gap: "4px",
          }}
        >
          <div style={{ fontWeight: "bold", display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <span>Quote Error: [{errorMessage.code}]</span>
          </div>
          <div>{errorMessage.message}</div>
          {errorMessage.code === "backend_unconfigured" || errorMessage.code === "backend_unavailable" ? (
            <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--color-text-secondary)" }}>
              The local quote engine is currently unavailable for ad hoc queries. Pre-computed historical reports remain fully accessible in the Historical workspace.
            </div>
          ) : null}
        </div>
      )}

      {/* Submit Button */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          isLoading={isLoading}
          disabled={isLoading || selectedSources.length === 0}
        >
          {isLoading ? "Running Historical Solver..." : "Execute Historical Quote"}
        </Button>
      </div>
    </form>
  );
};
