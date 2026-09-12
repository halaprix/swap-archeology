import { RouteFlowGraph } from "./routeFlowGraph";

export interface RouteFlowLayout {
  stepColumns: number[];
  maxStepColumn: number;
}

/** Place every operation one column after its latest consumed inventory lot. */
export function buildRouteFlowLayout(graph: RouteFlowGraph, stepCount: number): RouteFlowLayout {
  const stepColumns = Array.from({ length: stepCount }, () => 1);

  for (let stepIndex = 0; stepIndex < stepCount; stepIndex++) {
    const incomingColumns = graph.edges
      .filter((edge) => edge.toStepIndex === stepIndex && edge.fromStepIndex !== undefined)
      .map((edge) => stepColumns[edge.fromStepIndex!] + 1);
    stepColumns[stepIndex] = incomingColumns.length > 0 ? Math.max(...incomingColumns) : 1;
  }

  return { stepColumns, maxStepColumn: Math.max(1, ...stepColumns) };
}
