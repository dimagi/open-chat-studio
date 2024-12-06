import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Edge,
  EdgeChange,
  getConnectedEdges,
  Node,
  NodeChange,
} from "reactflow";
import { create } from "zustand";
import { PipelineStoreType } from "../types/pipelineStore";
import usePipelineManagerStore from "./pipelineManagerStore";
import useEditorStore from "./editorStore";
import { getNodeId } from "../utils";
import { cloneDeep } from "lodash";

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
    if (typeof nodeId === "string" && !nodeId) {
      return
    } else if (Array.isArray(nodeId) && !nodeId.length) {
      return
    }

    useEditorStore.getState().closeEditor();
    const nodes = get().nodes.filter((node) =>
        typeof nodeId === "string"
          ? node.id === nodeId
          : nodeId.includes(node.id)
      )
    const connectedEdges = getConnectedEdges(nodes, get().edges);
    const remainingEdges = get().edges.filter(
      (edge) => !connectedEdges.includes(edge),
    );
    get().setEdges(remainingEdges);

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
  clearEdgeLabels: () => {
    // Not calling setEdges so we don't autoSave
    set({
      edges: get().edges.map(
        (edge) => {
          delete edge.label;
          delete edge.type;
          return edge;
        }
      )
    });
  },
  setEdgeLabel: (sourceId, outputHandle, label) => {
    // Not calling setEdges so we don't autoSave
    set({
      edges: get().edges.map(
          (edge) => {
            if (sourceId == edge.source) {
              if (!outputHandle || edge.sourceHandle === outputHandle) {
                edge.label = label;
                edge.type = 'annotatedEdge';
              }
            }
            return edge;
          }
        )
    });
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
      );
  },
  addNode: (node, position) => {
    let minimumX = Infinity;
    let minimumY = Infinity;
    let newNodes: Node[] = get().nodes;

    if (node.position.y < minimumY) {
      minimumY = node.position.y;
    }
    if (node.position.x < minimumX) {
      minimumX = node.position.x;
    }

    const insidePosition = position.paneX
      ? {x: position.paneX + position.x, y: position.paneY! + position.y}
      : get().reactFlowInstance!.screenToFlowPosition({
        x: position.x,
        y: position.y,
      });
    const actualPosition = {
      x: insidePosition.x + node.position!.x - minimumX,
      y: insidePosition.y + node.position!.y - minimumY,
    }
    while (newNodes.some(node => node.position.x === actualPosition.x && node.position.y === actualPosition.y)) {
      actualPosition.x += 50;
      actualPosition.y += 50;
    }

    const newId = getNodeId(node.data.type);

    // Create a new node object
    const newNode = {
      id: newId,
      type: node.type,
      position: actualPosition,
      data: {
        ...cloneDeep(node.data),
        id: newId,
      },
    };

    // Add the new node to the list of nodes in state
    newNodes = newNodes
      .map((node) => ({...node, selected: false}))
      .concat({...newNode, selected: false});
    get().setNodes(newNodes);
  },
  resetFlow: ({ nodes, edges }) => {
    set({
      nodes,
      edges,
    });
  },
}));

export default usePipelineStore;
