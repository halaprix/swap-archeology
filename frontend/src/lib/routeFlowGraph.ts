/**
 * Truthful Ordered Route Flow Graph Builder
 *
 * Simulates route execution using a FIFO token inventory lot model.
 * Produces an exact directed graph preserving multi-hop paths, split/merge branches,
 * repeated pool steps, and identifying data shortfalls without inventing edges.
 */

import { QuoteRequestDetails, RouteStep } from "./types";

export interface InventoryLot {
  id: string;
  token: string; // lowercase address
  amount: bigint;
  sourceNodeId: string; // "input" | `step-${number}`
  stepIndex?: number;
}

export interface FlowNode {
  id: string; // "input" | `step-${number}` | "output"
  type: "input" | "step" | "output";
  label: string;
  stepIndex?: number;
  step?: RouteStep;
  tokenAddress?: string;
}

export interface FlowEdge {
  id: string;
  from: string; // node ID
  to: string;   // node ID
  tokenAddress: string;
  amount: bigint;
  amountRaw: string;
  fromStepIndex?: number;
  toStepIndex?: number;
  isInputEdge: boolean;
  isOutputEdge: boolean;
}

export interface FlowShortfall {
  stepIndex: number;
  tokenAddress: string;
  missingAmountRaw: string;
}

export interface FlowLeftover {
  tokenAddress: string;
  amountRaw: string;
  sourceNodeId: string;
}

export interface RouteFlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
  shortfalls: FlowShortfall[];
  leftovers: FlowLeftover[];
}

/**
 * Replays route steps as FIFO inventory lots seeded from the request input.
 */
export function buildRouteFlowGraph(
  request: QuoteRequestDetails,
  steps: RouteStep[] = []
): RouteFlowGraph {
  const zero = BigInt(0);
  const inputToken = (request.token_in || "").toLowerCase().trim();
  const targetToken = (request.token_out || "").toLowerCase().trim();

  const nodes: FlowNode[] = [
    {
      id: "input",
      type: "input",
      label: request.symbol_in || "INPUT",
      tokenAddress: request.token_in,
    },
  ];

  for (let idx = 0; idx < steps.length; idx++) {
    nodes.push({
      id: `step-${idx}`,
      type: "step",
      label: `Step #${idx + 1}`,
      stepIndex: idx,
      step: steps[idx],
    });
  }

  nodes.push({
    id: "output",
    type: "output",
    label: request.symbol_out || "OUTPUT",
    tokenAddress: request.token_out,
  });

  const edges: FlowEdge[] = [];
  const shortfalls: FlowShortfall[] = [];
  let edgeSeq = 0;

  // Initial inventory lot seeded from input
  const initialAmount = BigInt(request.amount_in || "0");
  const lots: InventoryLot[] = [
    {
      id: "lot-in-0",
      token: inputToken,
      amount: initialAmount,
      sourceNodeId: "input",
    },
  ];

  // Replay steps in strict order
  for (let idx = 0; idx < steps.length; idx++) {
    const step = steps[idx];
    const stepNodeId = `step-${idx}`;
    const tokenIn = (step.token_in || "").toLowerCase().trim();
    let needed = BigInt(step.amount_in || "0");

    // Consume available lots of tokenIn in FIFO order
    for (const lot of lots) {
      if (lot.token === tokenIn && lot.amount > zero) {
        const take = lot.amount < needed ? lot.amount : needed;
        lot.amount -= take;
        needed -= take;

        edges.push({
          id: `edge-${edgeSeq++}`,
          from: lot.sourceNodeId,
          to: stepNodeId,
          tokenAddress: step.token_in,
          amount: take,
          amountRaw: take.toString(),
          fromStepIndex: lot.stepIndex,
          toStepIndex: idx,
          isInputEdge: lot.sourceNodeId === "input",
          isOutputEdge: false,
        });

        if (needed === zero) {
          break;
        }
      }
    }

    // If needed > 0, record shortfall gap instead of inventing an edge
    if (needed > zero) {
      shortfalls.push({
        stepIndex: idx,
        tokenAddress: step.token_in,
        missingAmountRaw: needed.toString(),
      });
    }

    // Produce output lot for this step
    const tokenOut = (step.token_out || "").toLowerCase().trim();
    const produced = BigInt(step.amount_out || "0");
    lots.push({
      id: `lot-out-${idx}`,
      token: tokenOut,
      amount: produced,
      sourceNodeId: stepNodeId,
      stepIndex: idx,
    });
  }

  // Connect remaining lots matching final target token to "output"
  for (const lot of lots) {
    if (lot.token === targetToken && lot.amount > zero) {
      edges.push({
        id: `edge-${edgeSeq++}`,
        from: lot.sourceNodeId,
        to: "output",
        tokenAddress: request.token_out,
        amount: lot.amount,
        amountRaw: lot.amount.toString(),
        fromStepIndex: lot.stepIndex,
        isInputEdge: false,
        isOutputEdge: true,
      });
      lot.amount = zero;
    }
  }

  // Record any unconsumed non-output tokens as leftovers
  const leftovers: FlowLeftover[] = [];
  for (const lot of lots) {
    if (lot.amount > zero) {
      leftovers.push({
        tokenAddress: lot.token,
        amountRaw: lot.amount.toString(),
        sourceNodeId: lot.sourceNodeId,
      });
    }
  }

  return {
    nodes,
    edges,
    shortfalls,
    leftovers,
  };
}
