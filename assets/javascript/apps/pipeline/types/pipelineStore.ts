import {Connection, Edge, Node, OnEdgesChange, OnNodesChange, ReactFlowInstance} from "reactflow";
import {NodeData} from "./nodeParams";

/**
 * A node about to be dropped on the canvas. `addNode` assigns the final id and position,
 * so neither is meaningful on the way in.
 */
export type NewNode = {
  id?: string;
  type?: string;
  position: { x: number; y: number };
  data: NodeData & { id?: string };
};

export type PipelineStoreType = {
  reactFlowInstance: ReactFlowInstance | null;
  setReactFlowInstance: (newState: ReactFlowInstance) => void;
  nodes: Node[];
  edges: Edge[];
  readOnly: boolean;
  setReadOnly: (value: boolean) => void;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  setNodes: (update: Node[] | ((oldState: Node[]) => Node[])) => void;
  setEdges: (update: Edge[] | ((oldState: Edge[]) => Edge[])) => void;
  setEdgeLabel: (sourceId: string, outputHandle: string | null | undefined, label: string) => void;
  clearEdgeLabels: () => void;
  setNode: (id: string, update: Node | ((oldState: Node) => Node)) => void;
  getNode: (id: string) => Node | undefined;
  deleteNode: (nodeId: string | Array<string>) => void;
  deleteEdge: (edgeId: string | Array<string>) => void;
  onConnect: (connection: Connection) => void;
  addNode: (
    node: NewNode,
    position: { x: number; y: number; paneX?: number; paneY?: number }
  ) => void;
  resetFlow: (flow: {
    nodes: Node[];
    edges: Edge[];
  }) => void;
  undoLastChange: () => void;
  redoLastChange: () => void;
};
