import {Edge, Node} from "@reactflow/core";

export type ReactFlowJsonObject<NodeData = any, EdgeData = any> = {
    nodes: Node<NodeData>[];
    edges: Edge<EdgeData>[];
};

/**
 * A node still pointing at a deprecated LLM model. Advisory: the model keeps working until it is
 * removed, so this never makes the pipeline invalid. `replacement` is null when none is declared.
 */
export type DeprecatedModel = {
  model: string;
  replacement: string | null;
};

export type DeprecatedModelsType = {[nodeId: string]: DeprecatedModel};

export type PipelineType = {
  id: bigint;
  team: string;
  name: string;
  data: ReactFlowJsonObject | null;
  description: string;
  date_created?: string;
  updated_at?: string;
  errors: {[nodeId: string]: {[name: string]: string}},
  deprecated_models?: DeprecatedModelsType;
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
  deprecated_models?: DeprecatedModelsType;
  edit_revision: number;
};
