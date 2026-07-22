import {Edge, Node} from "reactflow";
import {NodeDiff, EdgeDiff, PipelineDiffPayload} from "./types/pipeline";

/**
 * Compute a semantic diff between the old pipeline graph and the new one.
 *
 * Returns a PipelineDiffPayload ready to send to the PATCH endpoint,
 * or null if there are no meaningful differences.
 *
 * React Flow transient state (selection, dragging) is ignored.
 */
export function computePipelineDiff(
  oldNodes: Node[],
  newNodes: Node[],
  oldEdges: Edge[],
  newEdges: Edge[],
  baseRevision: number,
  name?: string | null,
): PipelineDiffPayload | null {
  const nodeDiff = computeNodeDiff(oldNodes, newNodes);
  const edgeDiff = computeEdgeDiff(oldEdges, newEdges);

  if (nodeDiff === null && edgeDiff === null && name === undefined) {
    return null;
  }

  const payload: PipelineDiffPayload = {
    base_revision: baseRevision,
  };

  if (nodeDiff) {
    payload.nodes = nodeDiff;
  }
  if (edgeDiff) {
    payload.edges = edgeDiff;
  }
  if (name !== undefined) {
    payload.name = name;
  }

  return payload;
}

function computeNodeDiff(oldNodes: Node[], newNodes: Node[]): NodeDiff | null {
  const oldMap = new Map(oldNodes.map((n) => [n.id, n]));
  const newMap = new Map(newNodes.map((n) => [n.id, n]));

  const add: Array<Record<string, unknown>> = [];
  const update: Array<Record<string, unknown>> = [];
  const del: string[] = [];

  // Detect additions and updates
  for (const [id, newNode] of newMap) {
    const oldNode = oldMap.get(id);
    if (!oldNode) {
      // New node
      add.push(serializeNode(newNode));
    } else if (hasNodeChanged(oldNode, newNode)) {
      // Updated node
      update.push(serializeNode(newNode));
    }
  }

  // Detect deletions
  for (const [id] of oldMap) {
    if (!newMap.has(id)) {
      del.push(id);
    }
  }

  if (add.length === 0 && update.length === 0 && del.length === 0) {
    return null;
  }

  return {add, update, delete: del};
}

function computeEdgeDiff(oldEdges: Edge[], newEdges: Edge[]): EdgeDiff | null {
  const oldMap = new Map(oldEdges.map((e) => [e.id, e]));
  const newMap = new Map(newEdges.map((e) => [e.id, e]));

  const add: Array<Record<string, unknown>> = [];
  const update: Array<Record<string, unknown>> = [];
  const del: string[] = [];

  for (const [id, newEdge] of newMap) {
    const oldEdge = oldMap.get(id);
    if (!oldEdge) {
      add.push(serializeEdge(newEdge));
    } else if (hasEdgeChanged(oldEdge, newEdge)) {
      update.push(serializeEdge(newEdge));
    }
  }

  for (const [id] of oldMap) {
    if (!newMap.has(id)) {
      del.push(id);
    }
  }

  if (add.length === 0 && update.length === 0 && del.length === 0) {
    return null;
  }

  return {add, update, delete: del};
}

/**
 * Serialize a React Flow node to backend-compatible shape.
 * Strips transient React Flow properties.
 */
function serializeNode(node: Node): Record<string, unknown> {
  const {id, type, position, data} = node;
  return {
    id,
    type,
    position,
    data: {
      id: data?.id || id,
      type: data?.type,
      label: data?.label || "",
      params: data?.params || {},
    },
  };
}

/**
 * Serialize a React Flow edge to backend-compatible shape.
 */
function serializeEdge(edge: Edge): Record<string, unknown> {
  const {id, source, target, sourceHandle, targetHandle} = edge;
  return {
    id,
    source,
    target,
    sourceHandle: sourceHandle ?? "output",
    targetHandle: targetHandle ?? "input",
  };
}

/**
 * Check whether a node's meaningful properties have changed.
 * Ignores transient React Flow state like `selected`, `dragging`, `positionAbsolute`.
 */
function hasNodeChanged(oldNode: Node, newNode: Node): boolean {
  // Position is meaningful for the graph
  if (
    oldNode.position?.x !== newNode.position?.x ||
    oldNode.position?.y !== newNode.position?.y
  ) {
    return true;
  }

  // Data comparison (params, label, type)
  const oldData = oldNode.data || {};
  const newData = newNode.data || {};
  if (oldData.type !== newData.type) return true;
  if ((oldData.label || "") !== (newData.label || "")) return true;

  // Deep compare params
  const oldParams = oldData.params || {};
  const newParams = newData.params || {};
  if (!deepEqual(oldParams, newParams)) return true;

  return false;
}

/**
 * Check whether an edge's meaningful properties have changed.
 */
function hasEdgeChanged(oldEdge: Edge, newEdge: Edge): boolean {
  if (oldEdge.source !== newEdge.source) return true;
  if (oldEdge.target !== newEdge.target) return true;
  if ((oldEdge.sourceHandle ?? "output") !== (newEdge.sourceHandle ?? "output")) return true;
  if ((oldEdge.targetHandle ?? "input") !== (newEdge.targetHandle ?? "input")) return true;
  if ((oldEdge.label ?? "") !== (newEdge.label ?? "")) return true;
  if ((oldEdge.type ?? "") !== (newEdge.type ?? "")) return true;
  return false;
}

/**
 * Simple deep equality check for JSON-serializable values.
 */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;

  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((val, idx) => deepEqual(val, b[idx]));
  }

  if (typeof a === "object" && typeof b === "object") {
    const keysA = Object.keys(a as Record<string, unknown>);
    const keysB = Object.keys(b as Record<string, unknown>);
    if (keysA.length !== keysB.length) return false;
    return keysA.every((key) =>
      deepEqual((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]),
    );
  }

  return false;
}
