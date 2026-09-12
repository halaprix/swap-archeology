"use client";

import Link from "next/link";
import { LiquidityGapExplorer } from "@/components/history/LiquidityGapExplorer";

export default function OctoberGapPage() {
  return (
    <LiquidityGapExplorer
      sourcesUrl="/october-sources.json"
      oracleReferencesUrl="/october-oracle-references.json"
      csvUrl="/october-sources.csv"
      eyebrow="October 10, 2025 · Ethereum mainnet"
      title="Direct source comparison"
      subtitle="Selling ETH/WETH for USDC, every block from 21:14 to 22:05 UTC. Family lines show the highest positive quote among its direct pools at the selected size; they are not routes. ETH and WETH aggregates remain separate and no wrapping edge is added."
      headerTop={
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", marginBottom: "var(--space-2)" }}>
          <Link
            href="/crash-gaps"
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
            <span>Compare all 5 Binance crash liquidity gaps →</span>
          </Link>
        </div>
      }
    />
  );
}
