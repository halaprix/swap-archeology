"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { LiquidityGapExplorer } from "@/components/history/LiquidityGapExplorer";
import {
  CrashIndexEntry,
  DEFAULT_FIVE_CRASHES,
  parseFiveCrashIndex,
  statusBadgeVariant,
} from "@/lib/liquidityGaps";

export default function CrashGapsPage() {
  const [crashes, setCrashes] = useState<CrashIndexEntry[]>(DEFAULT_FIVE_CRASHES);
  const [selectedCrashId, setSelectedCrashId] = useState<string>("crash-1");

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    fetch("/five-crash-liquidity/index.json", { signal })
      .then(async (response) => {
        if (!response.ok) return;
        const parsed = parseFiveCrashIndex(await response.json());
        if (parsed?.crashes?.length && !signal.aborted) {
          setCrashes(parsed.crashes);
        }
      })
      .catch(() => {
        // Fallback remains DEFAULT_FIVE_CRASHES
      });

    return () => {
      controller.abort();
    };
  }, []);

  const selectedCrash = useMemo(() => {
    return crashes.find((c) => c.id === selectedCrashId) ?? crashes[0] ?? DEFAULT_FIVE_CRASHES[0];
  }, [crashes, selectedCrashId]);

  const statusVariant = statusBadgeVariant(selectedCrash.status);

  const crashOptions = useMemo(() => {
    return crashes.map((c) => ({
      value: c.id,
      label: `${c.label} [${c.status}]`,
    }));
  }, [crashes]);

  return (
    <LiquidityGapExplorer
      key={selectedCrash.id}
      sourcesUrl={selectedCrash.sourcesUrl}
      oracleReferencesUrl={selectedCrash.oracleReferencesUrl}
      csvUrl={selectedCrash.csvUrl}
      eyebrow={`${selectedCrash.label} · Ethereum Mainnet`}
      title="Binance Crash Liquidity Gap Explorer"
      subtitle={`Direct liquidity sources selling ETH/WETH for USDC across the ${selectedCrash.label} crash window (${selectedCrash.startUtc} to ${selectedCrash.endUtc} UTC, stride 1). Native ETH and WETH routes remain separate: ETH wrapping into WETH is not modeled, so the native ETH series does not represent full market liquidity.`}
      badges={
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
          <Badge variant={statusVariant}>{String(selectedCrash.status).toUpperCase()}</Badge>
          <Badge variant="warning">Collection-model-only</Badge>
          <Badge variant="neutral">Gas excluded</Badge>
          {selectedCrash.csvUrl && (
            <a href={selectedCrash.csvUrl} download style={{ fontSize: "var(--font-size-xs)", color: "var(--color-info-text)" }}>
              Download CSV
            </a>
          )}
        </div>
      }
      headerTop={
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBottom: "var(--space-2)",
            paddingBottom: "var(--space-2)",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <Link
            href="/october-gap"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-teal)",
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              fontWeight: "var(--font-weight-medium)",
            }}
          >
            <span>← Standalone October 10 Baseline Deep-Dive (/october-gap)</span>
          </Link>
          <span
            className="font-mono"
            style={{
              fontSize: "11px",
              color: "var(--color-text-muted)",
            }}
          >
            Five dates: 2025 Feb 3 · Feb 25 · Apr 7 · Jun 21 · Oct 10
          </span>
        </div>
      }
      headerExtra={
        <div style={{ marginTop: "var(--space-4)", display: "grid", gap: "var(--space-3)" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              gap: "var(--space-3)",
              alignItems: "end",
            }}
          >
            <Select
              label="Select Crash Event"
              value={selectedCrash.id}
              onChange={(e) => setSelectedCrashId(e.target.value)}
              options={crashOptions}
            />
            <div
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
                padding: "var(--space-2) var(--space-3)",
                backgroundColor: "var(--color-surface-muted)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border-subtle)",
              }}
            >
              <div>
                <strong>Window:</strong> {selectedCrash.startUtc} – {selectedCrash.endUtc} UTC
              </div>
              <div style={{ marginTop: "2px", color: "var(--color-text-muted)" }}>
                Trough anchor: <code>{selectedCrash.crashUtc}</code> (Binance minute close-second; not an exact trade execution timestamp).
              </div>
            </div>
          </div>

          {selectedCrash.id === "crash-5" && (
            <div
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-teal)",
                backgroundColor: "var(--color-teal-light)",
                border: "1px solid var(--color-teal-border)",
                padding: "var(--space-2) var(--space-3)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Note: The October 10, 2025 event has standalone every-block data on{" "}
              <Link href="/october-gap" style={{ color: "var(--color-teal)", fontWeight: "bold" }}>
                /october-gap
              </Link>.
            </div>
          )}
        </div>
      }
      emptyState={
        <Card>
          <CardHeader
            title={`${selectedCrash.label} · Collection pending`}
            subtitle={`Window: ${selectedCrash.startUtc} to ${selectedCrash.endUtc} UTC`}
            action={<Badge variant={statusVariant}>{String(selectedCrash.status).toUpperCase()}</Badge>}
          />
          <div style={{ padding: "var(--space-4)", display: "grid", gap: "var(--space-3)" }}>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--font-size-sm)" }}>
              Collection is pending for this crash event. When dataset collection completes, direct pool quotes and execution aggregates across the chosen UTC window will display here.
            </p>
            <p style={{ color: "var(--color-text-muted)", fontSize: "var(--font-size-xs)" }}>
              Trough anchor: <code>{selectedCrash.crashUtc}</code> (Binance minute close-second; not an exact trade execution timestamp).
            </p>
            <div>
              <Link href="/october-gap" style={{ fontSize: "var(--font-size-xs)", color: "var(--color-teal)" }}>
                View published October 10 baseline data on /october-gap →
              </Link>
            </div>
          </div>
        </Card>
      }
    />
  );
}
