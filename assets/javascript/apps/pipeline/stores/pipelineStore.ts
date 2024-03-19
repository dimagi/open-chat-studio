import {addEdge, applyEdgeChanges, applyNodeChanges, Edge, EdgeChange, Node, NodeChange,} from "reactflow";
import {create} from "zustand";
import {PipelineStoreType} from "../types/pipelineStore";
import usePipelineManagerStore from "./pipelineManagerStore";

const usePipelineStore = create<PipelineStoreType>((set, get) => ({
  nodes: [],
  edges: [],
  reactFlowInstance: null,
  setReactFlowInstance: (newState) => {
    set({reactFlowInstance: newState});
  },
  onNodesChange: (changes: NodeChange[]) => {
    set({
      nodes: applyNodeChanges(changes, get().nodes),
    });
  },
  onEdgesChange: (changes: EdgeChange[]) => {
    set({
      edges: applyEdgeChanges(changes, get().edges),
    });
  },
  setNodes: (change) => {
    const newChange = typeof change === "function" ? change(get().nodes) : change;
    const newEdges = get().edges;

    set({
      edges: newEdges,
      nodes: newChange,
    });

    const flowsManager = usePipelineManagerStore.getState();

    flowsManager.autoSaveCurrentPipline(
      newChange,
      newEdges,
      get().reactFlowInstance?.getViewport() ?? {x: 0, y: 0, zoom: 1}
    );
  },
  setEdges: (change) => {
    const newChange = typeof change === "function" ? change(get().edges) : change;

    set({
      edges: newChange,
    });

    const flowsManager = usePipelineManagerStore.getState();

    flowsManager.autoSaveCurrentPipline(
      get().nodes,
      newChange,
      get().reactFlowInstance?.getViewport() ?? {x: 0, y: 0, zoom: 1}
    );
  },
  setNode: (id: string, change: Node | ((oldState: Node) => Node)) => {
    const newChange =
      typeof change === "function"
        ? change(get().nodes.find((node) => node.id === id)!)
        : change;

    get().setNodes((oldNodes) =>
      oldNodes.map((node) => {
        if (node.id === id) {
          return newChange;
        }
        return node;
      })
    );
  },
  getNode: (id: string) => {
    return get().nodes.find((node) => node.id === id);
  },
  deleteNode: (nodeId) => {
    get().setNodes(
      get().nodes.filter((node) =>
        typeof nodeId === "string"
          ? node.id !== nodeId
          : !nodeId.includes(node.id)
      )
    );
  },
  deleteEdge: (edgeId) => {
    get().setEdges(
      get().edges.filter((edge) =>
        typeof edgeId === "string"
          ? edge.id !== edgeId
          : !edgeId.includes(edge.id)
      )
    );
  },
  onConnect: (connection) => {
    let newEdges: Edge[] = [];
    get().setEdges((oldEdges) => {
      newEdges = addEdge(connection, oldEdges);
      return newEdges;
    });
    usePipelineManagerStore
      .getState()
      .autoSaveCurrentPipline(
        get().nodes,
        newEdges,
        get().reactFlowInstance?.getViewport() ?? {x: 0, y: 0, zoom: 1}
      );
  },
  resetFlow: ({ nodes, edges, viewport }) => {
    set({
      nodes,
      edges,
    });
    get().reactFlowInstance!.setViewport(viewport);
  },
}));

export default usePipelineStore;
