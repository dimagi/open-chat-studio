import {Edge, Node} from "@reactflow/core/dist/esm/types";

export type ReactFlowJsonObject<NodeData = any, EdgeData = any> = {
    nodes: Node<NodeData>[];
    edges: Edge<EdgeData>[];
};

export type PipelineType = {
  id: bigint;
  team: string;
  name: string;
  data: ReactFlowJsonObject | null;
  description: string;
  date_created?: string;
  updated_at?: string;
  errors: {[nodeId: string]: {[name: string]: string}},
  edit_revision?: number;
};

/**
 * Semantic diff describing changes to pipeline nodes.
 */
export type NodeDiff = {
  add: Array<Record<string, unknown>>;
  update: Array<Record<string, unknown>>;
  delete: string[];
};

/**
 * Semantic diff describing changes to pipeline edges.
 */
export type EdgeDiff = {
  add: Array<Record<string, unknown>>;
  update: Array<Record<string, unknown>>;
  delete: string[];
};

/**
 * Payload sent to the PATCH endpoint for incremental pipeline saves.
 */
export type PipelineDiffPayload = {
  base_revision: number;
  nodes?: NodeDiff;
  edges?: EdgeDiff;
  name?: string | null;
};

/**
 * Response from the PATCH / POST pipeline save endpoint.
 */
export type PipelineSaveResponse = {
  data: Record<string, unknown>;
  errors: Record<string, unknown>;
  edit_revision: number;
};
