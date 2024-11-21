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
};
