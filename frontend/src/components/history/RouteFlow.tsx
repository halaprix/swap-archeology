"use client";

import React, { useId, useMemo, useState } from "react";
import { RouteStep, SwapReport } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { parsePoolId, shortenHash } from "@/lib/formatting";
import { getTokens } from "@/lib/catalog";
import { formatTokenAmount, resolveTokenMetadata } from "@/lib/tokenMetadata";
import { buildRouteFlowGraph } from "@/lib/routeFlowGraph";
import { buildRouteFlowLayout } from "@/lib/routeFlowLayout";
import { getVenueInfo } from "@/lib/venues";

export interface RouteFlowProps {
  steps: RouteStep[];
  report: SwapReport;
  selectedStepIndex: number | null;
  onSelectStep: (index: number) => void;
}

const SHARE_SCALE = 1_000_000n;

export const RouteFlow: React.FC<RouteFlowProps> = ({ steps, report, selectedStepIndex, onSelectStep }) => {
  const [hoveredStepIndex, setHoveredStepIndex] = useState<number | null>(null);
  const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const catalogTokens = useMemo(() => getTokens(), []);
  const graph = useMemo(() => buildRouteFlowGraph(report.request, steps), [report.request, steps]);
  const layout = useMemo(() => buildRouteFlowLayout(graph, steps.length), [graph, steps.length]);

  if (steps.length === 0) return <div style={{ padding: "var(--space-8)", textAlign: "center", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-sm)", border: "1px dashed var(--color-border-strong)" }}><div style={{ fontWeight: "var(--font-weight-semibold)" }}>No Route Found</div><p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)", marginTop: "4px" }}>The solver could not find an executable route within bounds for this block snapshot.</p></div>;

  const activeIndex = selectedStepIndex !== null && selectedStepIndex >= 0 && selectedStepIndex < steps.length ? selectedStepIndex : 0;
  const activeStep = steps[activeIndex];
  const activeParsedPool = parsePoolId(activeStep.pool_id);
  const metaIn = resolveTokenMetadata(report.request.token_in, catalogTokens, report.request);
  const metaOut = resolveTokenMetadata(report.request.token_out, catalogTokens, report.request);
  const columnWidth = 330, nodeWidth = 172, nodeHeight = 68, inputX = 28, stepStartX = 260;
  const outputX = stepStartX + layout.maxStepColumn * columnWidth;
  const svgWidth = outputX + 180, svgHeight = Math.max(290, steps.length * 84 + 108);
  const inputY = Math.round(svgHeight / 2 - nodeHeight / 2), outputY = inputY;
  const stepY = (index: number) => 46 + index * 84;
  const stepX = (index: number) => stepStartX + (layout.stepColumns[index] - 1) * columnWidth;

  // Propagate original-input fraction through output lots; geometry never compares raw units of different tokens.
  const stepShares = new Map<number, bigint>(), edgeShares = new Map<string, bigint>();
  const inputAmount = BigInt(report.request.amount_in || "0");
  for (let index = 0; index < steps.length; index++) {
    let share = 0n;
    for (const edge of graph.edges.filter((candidate) => candidate.toStepIndex === index)) {
      let edgeShare = 0n;
      if (edge.isInputEdge && inputAmount > 0n) edgeShare = (edge.amount * SHARE_SCALE) / inputAmount;
      else if (edge.fromStepIndex !== undefined) {
        const sourceTotal = BigInt(steps[edge.fromStepIndex].amount_out);
        if (sourceTotal > 0n) edgeShare = ((stepShares.get(edge.fromStepIndex) ?? 0n) * edge.amount) / sourceTotal;
      }
      edgeShares.set(edge.id, edgeShare); share += edgeShare;
    }
    stepShares.set(index, share);
  }
  for (const edge of graph.edges.filter((candidate) => candidate.isOutputEdge && candidate.fromStepIndex !== undefined)) {
    const sourceTotal = BigInt(steps[edge.fromStepIndex!].amount_out);
    edgeShares.set(edge.id, sourceTotal > 0n ? ((stepShares.get(edge.fromStepIndex!) ?? 0n) * edge.amount) / sourceTotal : 0n);
  }
  const maxShare = Array.from(edgeShares.values()).reduce((max, share) => share > max ? share : max, 1n);
  const venues = Array.from(new Map(steps.map((step) => { const family = parsePoolId(step.pool_id).family; return [family, getVenueInfo(family)]; })).values());
  const handleKeyDown = (event: React.KeyboardEvent, index: number) => {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); onSelectStep((index + 1) % steps.length); }
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); onSelectStep((index - 1 + steps.length) % steps.length); }
  };

  return <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
    <div role="group" aria-label="Route step sequence selection" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "var(--space-2)" }}>
      <span style={{ fontSize: "var(--font-size-xs)", fontWeight: "var(--font-weight-semibold)", color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Steps</span>
      {steps.map((step, index) => {
        const parsed = parsePoolId(step.pool_id), inMeta = resolveTokenMetadata(step.token_in, catalogTokens, report.request), outMeta = resolveTokenMetadata(step.token_out, catalogTokens, report.request), selected = activeIndex === index;
        return <button key={`step-${index}`} type="button" aria-pressed={selected} tabIndex={selected ? 0 : -1} onClick={() => onSelectStep(index)} onKeyDown={(event) => handleKeyDown(event, index)} style={{ padding: "3px 8px", fontSize: "var(--font-size-xs)", fontFamily: "var(--font-mono)", fontWeight: selected ? "var(--font-weight-bold)" : "var(--font-weight-medium)", backgroundColor: selected ? "var(--color-teal-light)" : "var(--color-surface)", border: `1px solid ${selected ? "var(--color-teal)" : "var(--color-border-strong)"}`, borderRadius: "var(--radius-xs)", color: selected ? "var(--color-teal)" : "var(--color-text)", cursor: "pointer" }}>#{index + 1} · {parsed.family.replace(/_/g, " ")} · {inMeta.symbol}→{outMeta.symbol}</button>;
      })}
    </div>

    <div style={{ background: "var(--route-panel)", border: "1px solid var(--route-panel-border)", borderRadius: "var(--radius-lg)", padding: "var(--space-4)", overflowX: "auto", boxShadow: "var(--shadow-lg)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}><div><div style={{ color: "var(--route-panel-text)", fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-sm)" }}>Execution route</div><div style={{ color: "var(--route-panel-muted)", fontSize: "11px", marginTop: "2px" }}>Follow the split. Explore each hop.</div></div><div aria-label="Venue legend" style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>{venues.map((venue) => <span key={venue.family} style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "var(--route-panel-muted)", fontSize: "10px" }}><i aria-hidden="true" style={{ width: "8px", height: "8px", borderRadius: "50%", background: venue.colorVar, boxShadow: `0 0 8px ${venue.colorVar}` }} />{venue.name}</span>)}</div></div>
      <svg role="img" aria-labelledby={`route-flow-title-${instanceId} route-flow-desc-${instanceId}`} viewBox={`0 0 ${svgWidth} ${svgHeight}`} width="100%" style={{ display: "block", minWidth: svgWidth, height: "auto" }}>
        <title id={`route-flow-title-${instanceId}`}>Ordered route execution flow</title><desc id={`route-flow-desc-${instanceId}`}>Input is on the left, dependent operations move right by execution depth, and final output is on the right.</desc>
        <defs><pattern id={`grid-${instanceId}`} width="12" height="12" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="var(--route-panel-grid)" /></pattern><filter id={`glow-${instanceId}`} x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
        <rect width={svgWidth} height={svgHeight} rx="10" fill={`url(#grid-${instanceId})`} />
        {graph.edges.map((edge) => {
          const fromX = edge.isInputEdge ? inputX + 116 : stepX(edge.fromStepIndex!) + nodeWidth, fromY = edge.isInputEdge ? inputY + nodeHeight / 2 : stepY(edge.fromStepIndex!) + nodeHeight / 2, toX = edge.isOutputEdge ? outputX : stepX(edge.toStepIndex!), toY = edge.isOutputEdge ? outputY + nodeHeight / 2 : stepY(edge.toStepIndex!) + nodeHeight / 2;
          const family = edge.toStepIndex !== undefined ? parsePoolId(steps[edge.toStepIndex].pool_id).family : parsePoolId(steps[edge.fromStepIndex!].pool_id).family, color = getVenueInfo(family).colorVar, selected = edge.toStepIndex === activeIndex || edge.fromStepIndex === activeIndex, share = edgeShares.get(edge.id) ?? 0n, strokeWidth = Math.max(3, Math.min(60, Number((share * 60n) / maxShare))), path = `M ${fromX} ${fromY} C ${fromX + 58} ${fromY}, ${toX - 58} ${toY}, ${toX} ${toY}`, tokenMeta = resolveTokenMetadata(edge.tokenAddress, catalogTokens, report.request);
          return <g key={edge.id} data-flow-edge={edge.id} data-flow-from={edge.from} data-flow-to={edge.to} data-flow-share={share.toString()}><path d={path} fill="none" stroke="rgba(255,255,255,0.10)" strokeWidth={strokeWidth + 4} strokeLinecap="round" /><path d={path} fill="none" stroke={color} opacity={selected ? 1 : 0.78} strokeWidth={strokeWidth} strokeLinecap="round" filter={selected ? `url(#glow-${instanceId})` : undefined}><title>{`${formatTokenAmount(edge.amountRaw, tokenMeta, 4)} ${tokenMeta.symbol}; ${Number((share * 10000n) / SHARE_SCALE) / 100}% of original input`}</title></path></g>;
        })}
        <g data-flow-node="input" data-flow-column="0" data-flow-x={inputX} transform={`translate(${inputX}, ${inputY})`}><rect width="116" height={nodeHeight} rx="9" fill="var(--route-panel-node)" stroke="var(--route-panel-border)" /><text x="14" y="19" fill="var(--route-panel-muted)" fontSize="9" fontFamily="var(--font-mono)" letterSpacing="1">INPUT</text><text x="14" y="38" fill="var(--route-panel-text)" fontSize="15" fontWeight="700">{metaIn.symbol}</text><text x="14" y="51" fill="var(--route-panel-muted)" fontSize="9" fontFamily="var(--font-mono)">{formatTokenAmount(report.request.amount_in, metaIn, 2)}</text></g>
        {steps.map((step, index) => {
          const parsed = parsePoolId(step.pool_id), venue = getVenueInfo(parsed.family), selected = activeIndex === index, hovered = hoveredStepIndex === index, inMeta = resolveTokenMetadata(step.token_in, catalogTokens, report.request), outMeta = resolveTokenMetadata(step.token_out, catalogTokens, report.request), x = stepX(index), y = stepY(index);
          return <g key={`node-${index}`} data-flow-node={`step-${index}`} data-flow-column={layout.stepColumns[index]} data-flow-x={x} transform={`translate(${x}, ${y})`} role="button" tabIndex={0} aria-label={`Step ${index + 1}: ${venue.name}, ${inMeta.symbol} to ${outMeta.symbol}`} style={{ cursor: "pointer" }} onClick={() => onSelectStep(index)} onMouseEnter={() => setHoveredStepIndex(index)} onMouseLeave={() => setHoveredStepIndex(null)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectStep(index); } else handleKeyDown(event, index); }}><rect width={nodeWidth} height={nodeHeight} rx="9" fill={selected ? "#353544" : "var(--route-panel-node)"} stroke={selected ? venue.colorVar : hovered ? "var(--route-panel-muted)" : "var(--route-panel-border)"} strokeWidth={selected ? 2 : 1} /><rect width="4" height={nodeHeight - 16} x="10" y="8" rx="2" fill={venue.colorVar} /><text x="24" y="19" fill="var(--route-panel-muted)" fontSize="9" fontFamily="var(--font-mono)">STEP {index + 1} · {shortenHash(parsed.address, 4, 3)}</text><text x="24" y="36" fill="var(--route-panel-text)" fontSize="12" fontWeight="700">{inMeta.symbol} → {outMeta.symbol}</text><text x="24" y="50" fill="var(--route-panel-muted)" fontSize="9" fontFamily="var(--font-mono)">{venue.name}</text></g>;
        })}
        <g data-flow-node="output" data-flow-column={layout.maxStepColumn + 1} data-flow-x={outputX} transform={`translate(${outputX}, ${outputY})`}><rect width="124" height={nodeHeight} rx="9" fill="#253d3a" stroke="var(--color-teal)" strokeWidth="1.5" /><text x="14" y="19" fill="#9ee5d8" fontSize="9" fontFamily="var(--font-mono)" letterSpacing="1">OUTPUT</text><text x="14" y="38" fill="var(--route-panel-text)" fontSize="15" fontWeight="700">{metaOut.symbol}</text><text x="14" y="51" fill="#9ee5d8" fontSize="9" fontFamily="var(--font-mono)">{report.best_split ? formatTokenAmount(report.best_split.amount_out, metaOut, 2) : "No route"}</text></g>
      </svg>
      <p style={{ color: "var(--route-panel-muted)", fontSize: "10px", marginTop: "var(--space-2)" }}>Band width shows relative input share. Select a hop for exact amounts.</p>
    </div>
    {graph.shortfalls.length > 0 && <div role="alert" style={{ padding: "var(--space-2) var(--space-3)", backgroundColor: "var(--color-warning-light)", border: "1px solid var(--color-warning-border)", borderRadius: "var(--radius-xs)", fontSize: "var(--font-size-xs)", color: "var(--color-warning-text)" }}><strong>Route Data Shortfall Detected:</strong> Step input inventory was not fully satisfied upstream; gaps are not drawn as synthetic bands.</div>}
    <div style={{ backgroundColor: "var(--color-surface-muted)", border: "1px solid var(--color-border)", borderRadius: "var(--radius-sm)", padding: "var(--space-3) var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "var(--space-2)", borderBottom: "1px solid var(--color-border)", paddingBottom: "var(--space-2)" }}><div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}><span style={{ fontSize: "var(--font-size-xs)", fontWeight: "var(--font-weight-bold)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Step #{activeIndex + 1} Exact Audit Inspector</span><Badge variant="venue" venueFamily={activeParsedPool.family}>{activeParsedPool.family.replace(/_/g, " ")}</Badge></div><span className="font-mono tabular-nums" style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>Sequence: {activeIndex + 1} of {steps.length}</span></div><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: "var(--space-3)", fontSize: "var(--font-size-xs)" }}><div><div style={{ color: "var(--color-text-muted)", textTransform: "uppercase", fontSize: "10px", letterSpacing: "0.05em" }}>Pool Identity</div><div className="font-mono" style={{ wordBreak: "break-all", marginTop: "2px" }}>{activeStep.pool_id}</div></div>{[{ label: "Input Leg", token: activeStep.token_in, amount: activeStep.amount_in }, { label: "Output Leg", token: activeStep.token_out, amount: activeStep.amount_out }].map((leg) => { const meta = resolveTokenMetadata(leg.token, catalogTokens, report.request); return <div key={leg.label}><div style={{ color: "var(--color-text-muted)", textTransform: "uppercase", fontSize: "10px", letterSpacing: "0.05em" }}>{leg.label} ({meta.symbol})</div><div className="font-mono tabular-nums" style={{ fontWeight: "bold", color: leg.label === "Output Leg" ? "var(--color-teal)" : "var(--color-text)", marginTop: "2px" }}>{formatTokenAmount(leg.amount, meta, 6)}</div><div className="font-mono" style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>raw: {leg.amount} ({meta.decimals !== null ? `${meta.decimals} dec` : "raw units"})</div></div>; })}</div></div>
  </div>;
};
