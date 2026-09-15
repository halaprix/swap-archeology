"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import {
  alignOracleSeries,
  bestDirectPool,
  KNOWN_ORACLE_SOURCES,
  linePath,
  type OracleReferencesData,
} from "@/lib/octoberSources";
import {
  familyBest,
  getFamilyLabel,
  type LiquidityGapData,
  type LiquidityGapRow,
  type Size,
} from "@/lib/liquidityGaps";
import { getVenueInfo } from "@/lib/venues";

type Series = {
  id: string;
  label: string;
  color: string;
  dash?: string;
  values: Array<number | null>;
  details?: Array<{ status: string; reason?: string }>;
};

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const utc = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });
const colors = ["#147d8b", "#6b4ea2", "#b45309", "#c2410c", "#2563eb", "#be185d", "#3f6212", "#475569"];
const PREFERRED_DIRECT_FAMILIES = ["uniswap_v2", "uniswap_v3", "pancake_v3", "uniswap_v4", "fluid_dex"];
const DIRECT_FAMILY_COLORS: Record<string, string> = {
  uniswap_v2: "#147d8b",
  uniswap_v3: "#6b4ea2",
  pancake_v3: "#d97706",
  uniswap_v4: "#b45309",
  fluid_dex: "#c2410c",
};

export const chartPriceFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

export function formatChartObservation(
  val: number | null | undefined,
  detail?: { status: string; reason?: string }
): { formatted: string; isAvailable: boolean } {
  if (typeof val === "number" && Number.isFinite(val) && val > 0) {
    return {
      formatted: `${chartPriceFmt.format(val)} USDC`,
      isAvailable: true,
    };
  }
  if (detail?.status) {
    return {
      formatted: `unavailable (${detail.status}${detail.reason ? `: ${detail.reason}` : ""})`,
      isAvailable: false,
    };
  }
  return {
    formatted: "unavailable",
    isAvailable: false,
  };
}

export function PriceChart({
  rows,
  series,
  selected,
  onSelect,
}: {
  rows: LiquidityGapRow[];
  series: Series[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const values = series
    .flatMap((entry) => entry.values)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));

  if (!values.length) return <p>No visible observations at this size.</p>;

  const w = 1100, h = 420, left = 62, right = 22, top = 18, bottom = 44;
  const lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi - lo, Math.abs(hi) * 0.005, 1);
  const start = new Date(rows[0].timestamp).getTime(), end = new Date(rows.at(-1)!.timestamp).getTime();
  const x = (index: number) => left + ((new Date(rows[index].timestamp).getTime() - start) / Math.max(1, end - start)) * (w - left - right);
  const y = (value: number) => h - bottom - ((value - lo) / span) * (h - top - bottom);

  const nearest = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const raw = ((event.clientX - box.left) / box.width) * w;
    let picked = 0, distance = Infinity;
    rows.forEach((_, index) => {
      const next = Math.abs(x(index) - raw);
      if (next < distance) {
        distance = next;
        picked = index;
      }
    });
    onSelect(picked);
  };

  const handleKeyDown = (event: React.KeyboardEvent<SVGSVGElement>) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setIsFocused(true);
      onSelect(Math.max(0, selected - 1));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setIsFocused(true);
      onSelect(Math.min(rows.length - 1, selected + 1));
    }
  };

  const ticks = [0.25, 0.5, 0.75].map((fraction) => start + (end - start) * fraction);

  const showTooltip = (isHovered || isFocused) && rows.length > 0 && selected >= 0 && selected < rows.length;
  const selectedRow = rows[selected];
  const xPercent = (x(selected) / w) * 100;
  const isRightHalf = xPercent > 50;

  return (
    <div style={{ overflowX: "auto" }}>
      <div
        style={{ position: "relative", minWidth: "720px", width: "100%" }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <svg
          viewBox={`0 0 ${w} ${h}`}
          style={{ minWidth: "720px", width: "100%", display: "block", cursor: "crosshair" }}
          role="img"
          tabIndex={0}
          aria-label="Selected source prices by recorded block timestamp. Use Left and Right arrow keys to navigate blocks."
          onClick={nearest}
          onMouseMove={(e) => {
            setIsHovered(true);
            nearest(e);
          }}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          onKeyDown={handleKeyDown}
        >
          <line x1={left} x2={w - right} y1={h - bottom} y2={h - bottom} stroke="var(--color-border-strong)" />
          {ticks.map((tick) => {
            const tickX = left + ((tick - start) / Math.max(1, end - start)) * (w - left - right);
            return (
              <g key={tick}>
                <line x1={tickX} x2={tickX} y1={top} y2={h - bottom} stroke="var(--color-border)" strokeDasharray="2 3" />
                <text x={tickX} y={h - 10} textAnchor="middle" fontSize="10" fill="var(--color-text-muted)">
                  {utc.format(new Date(tick))}
                </text>
              </g>
            );
          })}
          <line x1={x(selected)} x2={x(selected)} y1={top} y2={h - bottom} stroke="var(--color-text-muted)" strokeDasharray="3 3" />
          <text x="4" y={top + 5} fontSize="10" fill="var(--color-text-muted)">
            {fmt.format(hi)}
          </text>
          <text x="4" y={h - bottom} fontSize="10" fill="var(--color-text-muted)">
            {fmt.format(lo)}
          </text>
          <text
            x="14"
            y={(top + h - bottom) / 2}
            transform={`rotate(-90 14 ${(top + h - bottom) / 2})`}
            fontSize="10"
            fill="var(--color-text-muted)"
          >
            USDC per input token
          </text>
          <text x={left} y={h - 10} fontSize="10" fill="var(--color-text-muted)">
            {utc.format(new Date(start))}
          </text>
          <text x={w - right} y={h - 10} textAnchor="end" fontSize="10" fill="var(--color-text-muted)">
            {utc.format(new Date(end))}
          </text>
          {series.map((entry) => {
            const isReference = entry.id === "chainlink" || entry.id === "aave" || entry.id in KNOWN_ORACLE_SOURCES;
            return (
              <path
                key={entry.id}
                d={linePath(entry.values, (value, index) => ({ x: x(index), y: y(value ?? lo), value }))}
                fill="none"
                stroke={entry.color}
                strokeWidth={isReference ? 3.5 : 1.2}
                strokeOpacity={isReference ? 1 : 0.4}
                strokeDasharray={isReference ? entry.dash : "5 4"}
              />
            );
          })}
          {/* Visible selected value dots */}
          {series.map((entry) => {
            const val = entry.values[selected];
            if (typeof val !== "number" || !Number.isFinite(val) || val <= 0) return null;
            return (
              <circle
                key={`dot-${entry.id}`}
                cx={x(selected)}
                cy={y(val)}
                r="4.5"
                fill={entry.color}
                stroke="var(--color-surface, #ffffff)"
                strokeWidth="1.5"
                pointerEvents="none"
              />
            );
          })}
        </svg>

        {showTooltip && selectedRow && (
          <div
            style={{
              position: "absolute",
              top: "12px",
              ...(isRightHalf ? { left: "14px", right: "auto" } : { right: "14px", left: "auto" }),
              width: "min(360px, calc(100% - 24px))",
              maxHeight: "calc(100% - 24px)",
              overflowY: "auto",
              pointerEvents: "auto",
              zIndex: 10,
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-border-strong)",
              borderRadius: "var(--radius-md)",
              boxShadow: "var(--shadow-md)",
              padding: "var(--space-2-5) var(--space-3)",
            }}
          >
            <div
              style={{
                borderBottom: "1px solid var(--color-border-subtle)",
                paddingBottom: "var(--space-1-5)",
                marginBottom: "var(--space-2)",
                display: "grid",
                gap: "var(--space-0-5)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  gap: "var(--space-2)",
                }}
              >
                <span
                  className="font-mono"
                  style={{
                    fontWeight: "var(--font-weight-semibold)",
                    color: "var(--color-text)",
                    fontSize: "var(--font-size-xs)",
                  }}
                >
                  Block #{selectedRow.block}
                </span>
                <span
                  style={{
                    color: "var(--color-text-muted)",
                    fontSize: "10px",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    fontFamily: "var(--font-mono)",
                    whiteSpace: "nowrap",
                  }}
                >
                  USDC / token
                </span>
              </div>
              <div
                style={{
                  color: "var(--color-text-muted)",
                  fontSize: "11px",
                  wordBreak: "break-word",
                }}
              >
                {utc.format(new Date(selectedRow.timestamp))} UTC
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gap: "var(--space-1-5)",
              }}
            >
              {series.map((entry) => {
                const val = entry.values[selected];
                const detail = entry.details?.[selected];
                const { formatted, isAvailable } = formatChartObservation(val, detail);

                return (
                  <div
                    key={entry.id}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      justifyContent: "space-between",
                      gap: "var(--space-2)",
                      fontSize: "11px",
                      lineHeight: "1.4",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: "var(--space-1-5)",
                        minWidth: 0,
                        flex: 1,
                      }}
                    >
                      <span
                        style={{
                          width: "7px",
                          height: "7px",
                          borderRadius: "var(--radius-full)",
                          backgroundColor: entry.color,
                          marginTop: "4px",
                          flexShrink: 0,
                        }}
                      />
                      <span
                        style={{
                          color: "var(--color-text-secondary)",
                          wordBreak: "break-word",
                        }}
                      >
                        {entry.label}
                      </span>
                    </div>
                    <span
                      className="font-mono"
                      style={{
                        whiteSpace: "nowrap",
                        fontVariantNumeric: "tabular-nums",
                        fontWeight: isAvailable ? "var(--font-weight-medium)" : "var(--font-weight-regular)",
                        color: isAvailable ? "var(--color-text)" : "var(--color-text-muted)",
                        fontStyle: isAvailable ? "normal" : "italic",
                        flexShrink: 0,
                        marginLeft: "var(--space-2)",
                      }}
                    >
                      {formatted}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export interface LiquidityGapExplorerProps {
  sourcesUrl?: string;
  oracleReferencesUrl?: string;
  csvUrl?: string;
  eyebrow?: string;
  title?: string;
  subtitle?: string;
  badges?: React.ReactNode;
  headerTop?: React.ReactNode;
  headerExtra?: React.ReactNode;
  emptyState?: React.ReactNode;
}

export function LiquidityGapExplorer({
  sourcesUrl = "/october-sources.json",
  oracleReferencesUrl = "/october-oracle-references.json",
  csvUrl = "/october-sources.csv",
  eyebrow = "October 10, 2025 · Ethereum mainnet",
  title = "Direct source comparison",
  subtitle = "Selling ETH/WETH for USDC, every block from 21:14 to 22:05 UTC. Family lines show the highest positive quote among its direct pools at the selected size; they are not routes. ETH and WETH aggregates remain separate and no wrapping edge is added.",
  badges,
  headerTop,
  headerExtra,
  emptyState,
}: LiquidityGapExplorerProps) {
  const [prevSourcesUrl, setPrevSourcesUrl] = useState(sourcesUrl);
  const [data, setData] = useState<LiquidityGapData | null>(null);
  const [oracleData, setOracleData] = useState<OracleReferencesData | null>(null);
  const [loading, setLoading] = useState(Boolean(sourcesUrl));
  const [oracleLoading, setOracleLoading] = useState(Boolean(oracleReferencesUrl));
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<Size>("100");
  const [index, setIndex] = useState(0);

  if (sourcesUrl !== prevSourcesUrl) {
    setPrevSourcesUrl(sourcesUrl);
    setData(null);
    setOracleData(null);
    setError(null);
    setIndex(0);
    setLoading(Boolean(sourcesUrl));
    setOracleLoading(Boolean(oracleReferencesUrl));
  }

  const [visible, setVisible] = useState<Set<string>>(
    () =>
      new Set([
        ...PREFERRED_DIRECT_FAMILIES.filter((id) => sourcesUrl !== "/october-sources.json" || id !== "pancake_v3"),
        "aggregate:ETH",
        "aggregate:WETH",
        "chainlink",
        "aave",
        "oneinch_spot",
        "redstone_eth_usdc",
        "chaos_avalanche_eth_usdc",
        "chronicle_eth_usdc",
        "uniswap_v3_twap_300",
      ])
  );

  useEffect(() => {
    if (!sourcesUrl) return;

    const abortController = new AbortController();
    const { signal } = abortController;

    fetch(sourcesUrl, { signal })
      .then(async (response) => {
        if (!response.ok) throw Error(`Source dataset unavailable (${response.status})`);
        const value = (await response.json()) as LiquidityGapData;
        if (value.schemaVersion !== 1 || !value.rows?.length || !value.pools || !value.families) {
          throw Error("Unsupported source dataset schema");
        }
        if (!signal.aborted) {
          setData(value);
          setLoading(false);
        }
      })
      .catch((cause) => {
        if (signal.aborted) return;
        setLoading(false);
        setError(cause instanceof Error ? cause.message : "Could not load source dataset");
      });

    if (oracleReferencesUrl) {
      fetch(oracleReferencesUrl, { signal })
        .then(async (response) => {
          if (!response.ok) return;
          const value = (await response.json()) as OracleReferencesData;
          if (value.schemaVersion === 1 && Array.isArray(value.rows)) {
            if (!signal.aborted) {
              setOracleData(value);
            }
          }
        })
        .catch(() => {
          // Graceful missing/loading sidecar: must not break existing main chart
        })
        .finally(() => {
          if (!signal.aborted) {
            setOracleLoading(false);
          }
        });
    }

    return () => {
      abortController.abort();
    };
  }, [sourcesUrl, oracleReferencesUrl]);

  const boundedIndex = data ? Math.min(index, data.rows.length - 1) : 0;
  const row = data?.rows[boundedIndex];

  const toggle = (id: string) =>
    setVisible((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const directFamilies = useMemo(() => {
    if (!data?.pools) return PREFERRED_DIRECT_FAMILIES;
    const poolFamilies = new Set(data.pools.map((p) => p.family));
    const result: string[] = [];
    for (const fam of PREFERRED_DIRECT_FAMILIES) {
      if (poolFamilies.has(fam)) result.push(fam);
    }
    for (const fam of poolFamilies) {
      if (!result.includes(fam)) result.push(fam);
    }
    return result;
  }, [data]);

  const allFamilies = useMemo(() => {
    if (!data) return [];
    const list = [...data.families];
    for (const pool of data.pools) {
      if (!list.some((f) => f.id === pool.family)) {
        list.push({ id: pool.family, label: getVenueInfo(pool.family).name });
      }
    }
    return list;
  }, [data]);

  const oracleSourceIds = useMemo(() => Object.keys(KNOWN_ORACLE_SOURCES).filter(
    (id) => KNOWN_ORACLE_SOURCES[id].kind !== "oracle" || oracleData?.sources.some((source) => source.id === id)
  ), [oracleData]);

  const alignedOracle = useMemo(() => Object.fromEntries(
    oracleSourceIds.map((id) => [id, data?.rows ? alignOracleSeries(data.rows, oracleData, id) : []])
  ), [data, oracleData, oracleSourceIds]);

  const oracleSeries = useMemo(() => {
    const list: Series[] = [];
    const configs = oracleSourceIds.map((id) => ({ id, fallback: KNOWN_ORACLE_SOURCES[id] }));

    for (const { id, fallback } of configs) {
      if (visible.has(id)) {
        const meta = oracleData?.sources?.find((s) => s.id === id);
        const aligned = alignedOracle[id] ?? [];
        list.push({
          id,
          label: meta?.label ?? fallback.label,
          color: fallback.color,
          dash: fallback.dash,
          values: aligned.map((entry) => entry.price),
          details: aligned.map((entry) => ({ status: entry.status, reason: entry.reason })),
        });
      }
    }
    return list;
  }, [visible, oracleData, alignedOracle, oracleSourceIds]);

  const series: Series[] = useMemo(
    () =>
      !data
        ? []
        : [
            ...directFamilies
              .map((id, idx) => ({ id, index: idx }))
              .filter(({ id }) => visible.has(id))
              .map(({ id, index: idx }) => ({
                id,
                label: `${getFamilyLabel(id, data.families)} best direct pool`,
                color: DIRECT_FAMILY_COLORS[id] || colors[idx % colors.length],
                values: familyBest(data.rows, data.pools, id, size),
              })),
            ...data.pools
              .map((pool, idx) => ({ pool, index: idx }))
              .filter(({ pool }) => visible.has(`pool:${pool.id}`))
              .map(({ pool, index: idx }) => ({
                id: `pool:${pool.id}`,
                label: pool.label,
                color: colors[idx % colors.length],
                dash: idx >= colors.length ? "5 3" : undefined,
                values: data.rows.map((r) => r.pools[pool.id]?.[size]?.price ?? null),
              })),
            ...(visible.has("aggregate:WETH")
              ? [{ id: "aggregate:WETH", label: "WETH aggregate", color: "#0f766e", values: data.rows.map((r) => r.aggregates.WETH[size]) }]
              : []),
            ...(visible.has("aggregate:ETH")
              ? [{ id: "aggregate:ETH", label: "ETH aggregate", color: "#7c3aed", values: data.rows.map((r) => r.aggregates.ETH[size]) }]
              : []),
            ...(visible.has("chainlink")
              ? [{ id: "chainlink", label: "Chainlink", color: "#2563eb", values: data.rows.map((r) => r.chainlink) }]
              : []),
            ...(visible.has("aave")
              ? [{ id: "aave", label: "Aave", color: "#374151", values: data.rows.map((r) => r.aave) }]
              : []),
            ...oracleSeries,
          ],
    [data, size, visible, directFamilies, oracleSeries]
  );

  // Fallback for default October page when no custom emptyState is passed
  if (!emptyState) {
    if (error) {
      return (
        <main style={{ padding: "var(--space-8)" }}>
          <p role="alert" style={{ color: "var(--color-error)" }}>
            {error}
          </p>
        </main>
      );
    }
    if (!data || !row) {
      return <main style={{ padding: "var(--space-8)" }}>Loading saved October sources…</main>;
    }
  } else {
    // Custom empty state handling (e.g. for /crash-gaps)
    if (error || !data || !row) {
      return (
        <main style={{ maxWidth: "1440px", margin: "0 auto", padding: "var(--space-8) var(--space-6)", display: "grid", gap: "var(--space-6)" }}>
          {headerTop}
          <div>
            <p className="font-mono" style={{ color: "var(--color-teal)", fontSize: "var(--font-size-xs)", letterSpacing: ".08em", textTransform: "uppercase" }}>
              {eyebrow}
            </p>
            <h1 style={{ fontSize: "var(--font-size-3xl)" }}>{title}</h1>
            <p style={{ color: "var(--color-text-secondary)", maxWidth: 900 }}>{subtitle}</p>
            {badges}
            {headerExtra}
          </div>
          {loading ? (
            <Card>
              <CardHeader title="Loading Liquidity Sources" subtitle="Fetching canonical block observations from backend..." />
              <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--font-size-sm)" }}>
                Loading saved block source observations…
              </p>
            </Card>
          ) : (
            emptyState
          )}
        </main>
      );
    }
  }

  const poolBest = (family: string) =>
    bestDirectPool(
      data.pools.filter((pool) => pool.family === family),
      Object.fromEntries(data.pools.filter((pool) => pool.family === family).map((pool) => [pool.id, row.pools[pool.id]?.[size]]))
    );
  const familyLabel = (id: string) => getFamilyLabel(id, data.families);

  const getOracleLabel = (id: keyof typeof KNOWN_ORACLE_SOURCES) =>
    oracleData?.sources?.find((s) => s.id === id)?.label ?? KNOWN_ORACLE_SOURCES[id].label;

  const defaultBadges = (
    <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
      <Badge variant="warning">Collection-model-only</Badge>
      <Badge variant="info">Every block · {data.rows.length}</Badge>
      <Badge variant="neutral">Gas excluded</Badge>
    </div>
  );

  return (
    <main style={{ maxWidth: "1440px", margin: "0 auto", padding: "var(--space-8) var(--space-6)", display: "grid", gap: "var(--space-6)" }}>
      {headerTop}
      <div>
        <p className="font-mono" style={{ color: "var(--color-teal)", fontSize: "var(--font-size-xs)", letterSpacing: ".08em", textTransform: "uppercase" }}>
          {eyebrow}
        </p>
        <h1 style={{ fontSize: "var(--font-size-3xl)" }}>{title}</h1>
        <p style={{ color: "var(--color-text-secondary)", maxWidth: 900 }}>{subtitle}</p>
        {badges || defaultBadges}
        {headerExtra}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: "var(--space-3)" }}>
        <Select
          label="Trade size"
          value={size}
          onChange={(event) => setSize(event.target.value as Size)}
          options={data.scope.sizes.map((value) => ({ value, label: `${value} ETH / WETH` }))}
        />
        <label style={{ display: "grid", gap: "var(--space-1)", color: "var(--color-text-secondary)", fontSize: "var(--font-size-sm)" }}>
          Selected block #{row.block}
          <input
            aria-label="Selected block"
            type="range"
            min="0"
            max={data.rows.length - 1}
            value={boundedIndex}
            onChange={(event) => setIndex(Number(event.target.value))}
          />
        </label>
        {csvUrl && (
          <a href={csvUrl} download style={{ alignSelf: "end", color: "var(--color-info-text)" }}>
            Download CSV
          </a>
        )}
      </div>

      <Card>
        <CardHeader title="Visible price series" subtitle="Select a legend item; click or hover the chart to inspect its nearest recorded block." />
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", marginBottom: "var(--space-4)" }}>
          {[
            ...directFamilies.map((id) => ({ id, label: `${familyLabel(id)} best direct` })),
            { id: "aggregate:WETH", label: "WETH aggregate" },
            { id: "aggregate:ETH", label: "ETH aggregate" },
            { id: "chainlink", label: "Chainlink" },
            { id: "aave", label: "Aave" },
            ...oracleSourceIds.map((id) => ({ id, label: getOracleLabel(id) })),
          ].map((entry) => (
            <label key={entry.id} style={{ fontSize: "var(--font-size-xs)" }}>
              <input type="checkbox" checked={visible.has(entry.id)} onChange={() => toggle(entry.id)} /> {entry.label}
            </label>
          ))}
        </div>
        <PriceChart rows={data.rows} series={series} selected={boundedIndex} onSelect={setIndex} />
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", fontSize: "var(--font-size-xs)" }}>
          {series.map((entry) => (
            <span key={entry.id} style={{ color: entry.color }}>
              ● {entry.label}
            </span>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title={`Selected block #${row.block}`} subtitle={utc.format(new Date(row.timestamp))} />
        <p className="font-mono" style={{ minWidth: 0, overflowWrap: "anywhere", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
          Block hash {row.blockHash}
        </p>
        <div className="font-mono" style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", fontSize: "var(--font-size-sm)" }}>
          {series.map((entry) => {
            const val = entry.values[boundedIndex];
            const detail = entry.details?.[boundedIndex];
            const display =
              typeof val === "number" && Number.isFinite(val)
                ? fmt.format(val)
                : detail
                ? `unavailable (${detail.status}${detail.reason ? `: ${detail.reason}` : ""})`
                : "unavailable";
            return (
              <span key={entry.id}>
                {entry.label}: {display}
              </span>
            );
          })}
        </div>
        <div style={{ marginTop: "var(--space-4)", display: "grid", gap: "var(--space-2)", fontSize: "var(--font-size-xs)", minWidth: 0, overflowWrap: "anywhere" }}>
          {data.pools
            .filter((pool) => visible.has(`pool:${pool.id}`))
            .map((pool) => {
              const quote = row.pools[pool.id]?.[size];
              return (
                <div key={pool.id}>
                  {pool.label}: {quote?.price === null || quote?.price === undefined ? `unavailable${quote?.reason ? ` (${quote.reason})` : ""}` : fmt.format(quote.price)}
                  {poolBest(pool.family)?.id === pool.id ? " · best direct pool" : ""}
                </div>
              );
            })}
        </div>
      </Card>

      {row.aggregateRefunds && (
        <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
          Stablecoin proceeds exclude any returned DAI dust.{" "}
          {(["ETH", "WETH"] as const).map((token) => {
            const dust = Object.values(row.aggregateRefunds?.[token][size] ?? {}).reduce((sum, value) => sum + value, 0);
            return dust ? (
              <span key={token}>
                {token} route returns {(dust / 1e18).toFixed(18)} DAI.{" "}
              </span>
            ) : null;
          })}
        </p>
      )}

      <Card>
        <CardHeader
          title="Oracle-like reference sources"
          subtitle="Selling WETH for USDC benchmark rates. Reference rates, not executable swap quotes; sidecar provides underlying source and methodology details."
          action={
            <Badge variant={oracleData ? "success" : oracleLoading ? "warning" : "neutral"}>
              {oracleData ? `Sidecar loaded (${oracleData.rows.length} blocks)` : oracleLoading ? "Loading sidecar…" : "Sidecar pending/offline"}
            </Badge>
          }
        />
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: "var(--font-size-xs)", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Reference source</th>
                <th style={{ textAlign: "left" }}>Kind & window</th>
                <th style={{ textAlign: "left" }}>Selected block price</th>
                <th style={{ textAlign: "left" }}>Status / reason</th>
                <th style={{ textAlign: "left" }}>Provenance & contract</th>
              </tr>
            </thead>
            <tbody>
              {oracleSourceIds.map((sourceId) => {
                const meta = oracleData?.sources?.find((s) => s.id === sourceId);
                const fallback = KNOWN_ORACLE_SOURCES[sourceId];
                const alignedVal = alignedOracle[sourceId]?.[boundedIndex];
                const lbl = meta?.label ?? fallback.label;
                const kind = meta?.kind ?? fallback.kind;
                const windowSec = meta?.windowSeconds ?? fallback.windowSeconds;
                const desc = meta?.description ?? fallback.description;
                const addr = meta?.address ?? meta?.pool;
                const isAvailable = alignedVal && typeof alignedVal.price === "number" && Number.isFinite(alignedVal.price);
                return (
                  <tr key={sourceId} style={{ borderBottom: "1px solid var(--color-border)" }}>
                    <td style={{ padding: "var(--space-2) 0", fontWeight: 500 }}>
                      <span style={{ color: fallback.color }}>●</span> {lbl}
                    </td>
                    <td>
                      <Badge variant="neutral">
                        {kind.toUpperCase()}
                        {windowSec ? ` · ${windowSec}s` : kind === "oracle" ? " · latest reported" : " · instantaneous"}
                      </Badge>
                    </td>
                    <td className="font-mono">{isAvailable ? `${fmt.format(alignedVal.price!)} USDC` : "—"}</td>
                    <td>
                      {isAvailable ? (
                        <span><Badge variant="success">available</Badge>{alignedVal.reason ? ` (${alignedVal.reason})` : ""}</span>
                      ) : (
                        <span style={{ color: "var(--color-text-muted)" }}>
                          {alignedVal?.status ?? "unloaded"}
                          {alignedVal?.reason ? ` (${alignedVal.reason})` : ""}
                        </span>
                      )}
                    </td>
                    <td style={{ color: "var(--color-text-secondary)", maxWidth: "420px" }}>
                      <div>{desc}</div>
                      {addr && (
                        <div className="font-mono" style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>
                          {addr}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p style={{ marginTop: "var(--space-3)", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
          Notice: These comparison sources represent liquidity-weighted spot and geometric TWAP reference rates for USDC/WETH. They are not executable swap quotes through the routing engine. Exact block and block hash alignment is validated per block; the reference sidecar provides specific contract connector and method configuration details.
        </p>
      </Card>

      <Card>
        <CardHeader
          title="Source availability at selected block"
          subtitle="All requested families are retained. A missing direct quote is never plotted as zero."
        />
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: "var(--font-size-xs)", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Family</th>
                <th style={{ textAlign: "left" }}>Status</th>
                <th>Usable</th>
                <th>Direct</th>
                <th style={{ textAlign: "left" }}>Best direct pool</th>
              </tr>
            </thead>
            <tbody>
              {allFamilies.map((family) => {
                const availability = row.availability[family.id];
                const best = poolBest(family.id);
                const stat = availability?.status ?? "unavailable";
                return (
                  <tr key={family.id}>
                    <td style={{ padding: "var(--space-2) 0" }}>{familyLabel(family.id)}</td>
                    <td>
                      {stat}
                      {stat === "supported" && !availability?.directCount ? " · connector-only / no direct pair" : ""}
                    </td>
                    <td style={{ textAlign: "center" }}>{availability?.usableCount ?? 0}</td>
                    <td style={{ textAlign: "center" }}>{availability?.directCount ?? 0}</td>
                    <td>{best ? `${best.id} · ${fmt.format(best.price)}` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <details>
        <summary>Individual direct pools</summary>
        <div style={{ display: "grid", gap: "var(--space-3)", marginTop: "var(--space-3)", minWidth: 0 }}>
          {allFamilies.map((family) => {
            const pools = data.pools.filter((pool) => pool.family === family.id);
            return pools.length ? (
              <fieldset key={family.id} style={{ minWidth: 0 }}>
                <legend>{familyLabel(family.id)}</legend>
                {pools.map((pool) => (
                  <label key={pool.id} style={{ display: "block", minWidth: 0, overflowWrap: "anywhere", fontSize: "var(--font-size-xs)" }}>
                    <input type="checkbox" checked={visible.has(`pool:${pool.id}`)} onChange={() => toggle(`pool:${pool.id}`)} />{" "}
                    {pool.label} · {pool.inputSymbol} · {pool.feeBps === null ? "fee unavailable" : `${pool.feeBps} bps`} · {pool.address}
                  </label>
                ))}
              </fieldset>
            ) : null;
          })}
        </div>
      </details>
    </main>
  );
}
