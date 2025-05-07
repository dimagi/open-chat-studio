import {Connection, Edge, Node, OnEdgesChange, OnNodesChange, ReactFlowInstance} from "reactflow";

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
    node: any,
    position: { x: number; y: number; paneX?: number; paneY?: number }
  ) => void;
  resetFlow: (flow: {
    nodes: Node[];
    edges: Edge[];
  }) => void;
};
