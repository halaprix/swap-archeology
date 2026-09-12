"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { ReportDetail } from "@/components/history/ReportDetail";
import { CatalogReportEntry } from "@/lib/types";
import { CrashSliceRow, CrashSlicesData, amountKey, chronological, pairKey, parseCrashSlices } from "@/lib/crashSlices";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
const dateTime = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

function price(value: number | null): string {
  return value === null ? "Unavailable" : number.format(value);
}

function statusLabel(row: CrashSliceRow): string {
  if (row.oracleStatus === "unsupported_direct_market_feed") return "Unsupported direct market feed";
  if (row.oracleStatus === "missing_round") return "Missing Chainlink round";
  if (row.oracleStatus === "invalid_round") return "Invalid Chainlink round";
  return row.oracleStatus === "ok" ? "Chainlink round available" : "Chainlink round unavailable";
}

function oracleAge(seconds: number | null): string {
  if (seconds === null) return "Unavailable";
  return `${number.format(seconds / 3600)} h (${number.format(seconds)} s)`;
}

function baselinePrice(row: CrashSliceRow): number | null {
  return typeof row.singlePoolBaselinePrice === "number" ? row.singlePoolBaselinePrice : null;
}

function rowKey(row: CrashSliceRow): string {
  return `${row.sliceId}:${row.blockHash}:${row.tokenIn}:${row.tokenOut}:${row.amountRaw}`;
}

function DiscretePriceChart({ rows }: { rows: CrashSliceRow[] }) {
  const values = rows.flatMap((row) => [row.aggregatedPrice, baselinePrice(row), row.oraclePrice]).filter((value): value is number => value !== null && Number.isFinite(value));
  if (values.length === 0) return <p style={{ color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>No comparable execution and oracle observations for this filter.</p>;
  const width = 760, height = 230, left = 58, right = 18, top = 22, bottom = 38;
  const low = Math.min(...values), high = Math.max(...values), span = high - low || Math.max(Math.abs(high) * 0.02, 1);
  const x = (index: number) => left + index * (width - left - right) / Math.max(rows.length - 1, 1);
  const y = (value: number) => height - bottom - (value - low) / span * (height - top - bottom);
  return <div style={{ overflowX: "auto" }}><svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} style={{ minWidth: "560px", display: "block" }} role="img" aria-label="Discrete route execution and Chainlink cross-rate observations">
    <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} stroke="var(--color-border-strong)" />
    <text x={left} y={14} fill="var(--color-text-muted)" fontSize="10" fontFamily="var(--font-mono)">Price scale: {number.format(low)}–{number.format(high)}</text>
    {rows.map((row, index) => <g key={`${row.blockHash}-${row.amountRaw}`}>
      <line x1={x(index)} x2={x(index)} y1={top} y2={height - bottom} stroke="var(--color-border-subtle)" />
      {row.aggregatedPrice !== null && <circle cx={x(index)} cy={y(row.aggregatedPrice)} r="5" fill="var(--color-teal)" />}
      {baselinePrice(row) !== null && <path d={`M ${x(index)} ${y(baselinePrice(row)!)-5} l 5 5 l -5 5 l -5 -5 Z`} fill="var(--color-warning)" />}
      {row.oraclePrice !== null && <rect x={x(index) - 4} y={y(row.oraclePrice) - 4} width="8" height="8" fill="var(--color-info)" />}
      <text x={x(index)} y={height - 18} textAnchor="middle" fill="var(--color-text-muted)" fontSize="9" fontFamily="var(--font-mono)">#{row.block}</text>
    </g>)}
  </svg><div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", fontSize: "11px", color: "var(--color-text-secondary)" }}><span><b style={{ color: "var(--color-teal)" }}>●</b> Aggregated route execution price</span><span><b style={{ color: "var(--color-warning)" }}>◆</b> Single-pool baseline, when supplied</span><span><b style={{ color: "var(--color-info)" }}>■</b> Exact-same-block Chainlink cross-rate</span></div><p style={{ marginTop: "var(--space-2)", color: "var(--color-text-muted)", fontSize: "11px" }}>Prices are USDC per input token, before gas. Negative deviation means execution is below the oracle. Points are discrete observations, not a dense price scan.</p></div>;
}

function RouteDetail({ entry, entries, onSelect }: { entry: CatalogReportEntry | null; entries: CatalogReportEntry[]; onSelect: (id: string) => void }) {
  if (!entry) return <Card><CardHeader title="Selected Route Evidence" /><p style={{ color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>This saved row has no display-ready route record.</p></Card>;
  return <section><div style={{ marginBottom: "var(--space-3)" }}><Badge variant="warning">Collection-model-only route record</Badge></div><ReportDetail entry={entry} allReports={entries} onSelectReport={onSelect} /></section>;
}

export default function CrashSlicesPage() {
  const [data, setData] = useState<CrashSlicesData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pair, setPair] = useState("");
  const [amount, setAmount] = useState("");
  const [slice, setSlice] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { fetch("/crash-slices.json").then(async (response) => {
    if (!response.ok) throw new Error(`Static dataset unavailable (${response.status})`);
    const parsed = parseCrashSlices(await response.json());
    if (!parsed) throw new Error("Static dataset has an unsupported schema or qualification mode");
    setData(parsed);
    const initialSlice = parsed.slices.find((item) => /feb/i.test(`${item.id} ${item.label}`))?.id ?? parsed.slices[0]?.id ?? "";
    const inSlice = parsed.rows.filter((row) => row.sliceId === initialSlice);
    const initialPair = inSlice.find((row) => pairKey(row) === "WETH → USDC") ? "WETH → USDC" : pairKey(inSlice[0] ?? parsed.rows[0]);
    const initialRow = inSlice.find((row) => pairKey(row) === initialPair && row.amount === "100") ?? inSlice.find((row) => pairKey(row) === initialPair) ?? parsed.rows[0];
    setSlice(initialSlice); setPair(initialPair); setAmount(initialRow ? amountKey(initialRow) : ""); setSelected(initialRow ? rowKey(initialRow) : null);
  }).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load static dataset")); }, []);
  const rows = useMemo(() => data && slice && pair && amount ? chronological(data.rows).filter((row) => row.sliceId === slice && pairKey(row) === pair && amountKey(row) === amount) : [], [data, slice, pair, amount]);
  const selectedRow = rows.find((row) => rowKey(row) === selected) ?? rows[0] ?? null;
  const displayEntries = useMemo<CatalogReportEntry[]>(() => rows.filter((row): row is CrashSliceRow & { displayReport: CatalogReportEntry["report"] } => Boolean(row.displayReport)).map((row) => ({ id: `${row.blockHash}:${row.amountRaw}`, origin: "saved", report: row.displayReport })), [rows]);
  const selectedEntry = selectedRow?.displayReport ? displayEntries.find((entry) => entry.id === `${selectedRow.blockHash}:${selectedRow.amountRaw}`) ?? null : null;
  const pairs = data ? [...new Set(data.rows.filter((row) => row.sliceId === slice).map(pairKey))].sort() : [];
  const amounts = data ? [...new Map(data.rows.filter((row) => row.sliceId === slice && pairKey(row) === pair).map((row) => [amountKey(row), `${row.amount} ${row.tokenIn}`])).entries()] : [];
  return <main style={{ maxWidth: "1440px", margin: "0 auto", padding: "var(--space-8) var(--space-6)", display: "grid", gap: "var(--space-6)" }}>
      <div><p className="font-mono" style={{ color: "var(--color-teal)", fontSize: "var(--font-size-xs)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Historical crash observations</p><h1 style={{ fontSize: "var(--font-size-3xl)", marginTop: "var(--space-1)" }}>Crash slices</h1><p style={{ color: "var(--color-text-secondary)", marginTop: "var(--space-2)", maxWidth: "820px" }}>Saved routing-model observations compared with an exact-same-block Chainlink market cross-rate. This is collection-model-only research, not a live quote or execution claim.</p></div>
    {error && <div role="alert" style={{ padding: "var(--space-4)", background: "var(--color-error-light)", border: "1px solid var(--color-error-border)", color: "var(--color-error-text)", borderRadius: "var(--radius-sm)" }}>{error}</div>}
    {!data && !error && <p style={{ color: "var(--color-text-muted)" }}>Loading saved crash-slice observations…</p>}
    {data && <><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-3)", padding: "var(--space-3)", background: "var(--color-surface-muted)", border: "1px solid var(--color-border)", borderRadius: "var(--radius-sm)" }}><Select label="Crash window" value={slice} onChange={(event) => { const next = event.target.value; const nextRows = data.rows.filter((row) => row.sliceId === next); const nextPair = pairKey(nextRows[0]); const nextRow = nextRows[0]; setSlice(next); setPair(nextPair); setAmount(amountKey(nextRow)); setSelected(rowKey(nextRow)); }} options={data.slices.map((item) => ({ value: item.id, label: item.label }))} /><Select label="Token pair" value={pair} onChange={(event) => { const next = event.target.value; const nextRow = data.rows.find((row) => row.sliceId === slice && pairKey(row) === next); setPair(next); setAmount(nextRow ? amountKey(nextRow) : ""); setSelected(nextRow ? rowKey(nextRow) : null); }} options={pairs.map((value) => ({ value, label: value }))} /><Select label="Input amount" value={amount} onChange={(event) => { const next = event.target.value; const nextRow = data.rows.find((row) => row.sliceId === slice && pairKey(row) === pair && amountKey(row) === next); setAmount(next); setSelected(nextRow ? rowKey(nextRow) : null); }} options={amounts.map(([value, label]) => ({ value, label }))} /></div>
      <Card><CardHeader title={`Execution price (${pair}; fixed ${amounts.find(([value]) => value === amount)?.[1] ?? "input amount"}) against direct market reference`} /><DiscretePriceChart rows={rows} /></Card>
      <div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--font-size-xs)", background: "var(--color-surface)", border: "1px solid var(--color-border)" }}><caption style={{ textAlign: "left", padding: "var(--space-3)", fontWeight: "var(--font-weight-bold)" }}>Discrete saved observations ({rows.length})</caption><thead><tr style={{ background: "var(--color-surface-muted)", textAlign: "left" }}>{["Block / UTC", "Execution", "Baseline", "Oracle", "Deviation", "Oldest feed age", "Coverage"].map((heading) => <th key={heading} scope="col" style={{ padding: "var(--space-2) var(--space-3)", borderBottom: "1px solid var(--color-border)" }}>{heading}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={rowKey(row)} onClick={() => setSelected(rowKey(row))} tabIndex={0} role="button" onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(rowKey(row)); } }} style={{ cursor: "pointer", background: selectedRow && rowKey(selectedRow) === rowKey(row) ? "var(--color-teal-light)" : undefined, borderBottom: "1px solid var(--color-border-subtle)" }}><td style={{ padding: "var(--space-2) var(--space-3)" }}><div className="font-mono">#{row.block}</div><div style={{ color: "var(--color-text-muted)" }}>{dateTime.format(new Date(row.timestampISO))}</div></td><td className="font-mono" style={{ padding: "var(--space-2) var(--space-3)" }}>{price(row.aggregatedPrice)}</td><td className="font-mono" style={{ padding: "var(--space-2) var(--space-3)" }}>{price(baselinePrice(row))}</td><td style={{ padding: "var(--space-2) var(--space-3)" }}><div className="font-mono">{price(row.oraclePrice)}</div>{row.oraclePrice === null && <span style={{ color: "var(--color-warning-text)" }}>{statusLabel(row)}</span>}</td><td className="font-mono" style={{ padding: "var(--space-2) var(--space-3)" }}>{row.deviationBps === null ? "Unavailable" : `${number.format(row.deviationBps)} bps`}</td><td className="font-mono" style={{ padding: "var(--space-2) var(--space-3)" }}>{oracleAge(row.oracleAgeSeconds)}</td><td style={{ padding: "var(--space-2) var(--space-3)" }}><div>{row.sourceCoverage.selectedFamilies?.length ?? 0} selected families</div><div style={{ color: "var(--color-text-muted)" }}>{row.sourceCoverage.usablePools ?? "—"} usable pools · {row.sourceCoverage.unsupported?.length ?? 0} unsupported</div></td></tr>)}</tbody></table></div>
      {selectedRow && <RouteDetail entry={selectedEntry} entries={displayEntries} onSelect={(id) => { const match = rows.find((row) => `${row.blockHash}:${row.amountRaw}` === id); if (match) setSelected(rowKey(match)); }} />}<div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}><Badge variant="warning">Collection-model-only</Badge><Badge variant="info">Oracle ages are observed, not assumed fresh</Badge></div></>}
  </main>;
}
