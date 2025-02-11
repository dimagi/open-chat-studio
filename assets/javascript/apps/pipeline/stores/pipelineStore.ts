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
import {create, StateCreator} from "zustand";
import {PipelineStoreType} from "../types/pipelineStore";
import useEditorStore from "./editorStore";
import {getNodeId} from "../utils";
import {cloneDeep} from "lodash";
import {ErrorsType, PipelineManagerStoreType} from "../types/pipelineManagerStore";
import {apiClient} from "../api/api";
import {PipelineType} from "../types/pipeline";
import usePipelineManagerStore from "./pipelineManagerStore";

let saveTimeoutId: NodeJS.Timeout | null = null;


const usePipelineStore = create<PipelineStoreType & PipelineManagerStoreType>((...a) => ({
  ...createPipelineStore(...a),
  ...createPipelineManagerStore(...a),
}));

export default usePipelineStore;

const createPipelineStore: StateCreator<
  PipelineStoreType,
  [],
  [],
  PipelineStoreType
> = (set, get) => ({
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
    const data = cloneDeep(node.data);
    data.params["name"] = newId;
    // Create a new node object
    const newNode = {
      id: newId,
      type: node.type,
      position: actualPosition,
      data: {
        ...data,
        id: newId,
      },
    };

    // Add the new node to the list of nodes in state
    newNodes = newNodes
      .map((node) => ({...node, selected: false}))
      .concat({...newNode, selected: false});
    get().setNodes(newNodes);
  },
  resetFlow: ({nodes, edges}) => {
    set({
      nodes,
      edges,
    });
  },
})

const createPipelineManagerStore: StateCreator<PipelineManagerStoreType, [], [], PipelineManagerStoreType> = (set, get) => ({
  currentPipeline: undefined,
  currentPipelineId: undefined,
  dirty: false,
  isSaving: false,
  isLoading: true,
  errors: {},
  loadPipeline: async (pipelineId: number) => {
    set({isLoading: true});
    apiClient.getPipeline(pipelineId).then((pipeline) => {
      if (pipeline) {
        updateEdgeClasses(pipeline, pipeline.errors);
        set({currentPipeline: pipeline, currentPipelineId: pipelineId});
        set({errors: pipeline.errors});
        set({isLoading: false});
      }
    }).catch((e) => {
      console.log(e);
    });
  },
  updatePipelineName: (name: string) => {
    if (get().currentPipeline) {
      set({currentPipeline: {...get().currentPipeline!, name}});
    }
  },
  setIsLoading: (isLoading: boolean) => set({isLoading}),
  autoSaveCurrentPipline: (nodes: Node[], edges: Edge[]) => {
    set({dirty: true});
    // Clear the previous timeout if it exists.
    if (saveTimeoutId) {
      clearTimeout(saveTimeoutId);
    }

    // Set up a new timeout.
    saveTimeoutId = setTimeout(() => {
      if (get().currentPipeline) {
        get().savePipeline(
          {...get().currentPipeline!, data: {nodes, edges}},
          true,
        );
      }
    }, 2000); // Delay of 2s
  },
  savePipeline: (pipeline: PipelineType,) => {
    set({isSaving: true});
    if (saveTimeoutId) {
      clearTimeout(saveTimeoutId);
    }
    return new Promise<void>((resolve, reject) => {
      apiClient.updatePipeline(get().currentPipelineId!, pipeline)
        .then((response) => {
          if (response) {
            pipeline.data = response.data;
            updateEdgeClasses(pipeline, response.errors);
            set({currentPipeline: pipeline, dirty: false});
            set({errors: response.errors});
            resolve();
          }
        })
        .catch((err) => {
          alertify.error("There was an error saving");
          reject(err);
        }).finally(() => {
        set({isSaving: false});
      });
    });
  },
  nodeHasErrors: (nodeId: string) => {
    return !!get().errors["node"] && !!get().errors["node"]![nodeId];
  },
  getNodeFieldError: (nodeId: string, fieldName: string) => {
    const errors = get().errors;
    if (!errors["node"]) {
      return "";
    }
    const nodeErrors = errors["node"][nodeId];
    return nodeErrors ? nodeErrors[fieldName] : "";
  },
  edgeHasErrors: (edgeId: string) => {
    return !!get().errors["edge"] && get().errors["edge"]!.includes(edgeId);
  },
  getPipelineError: () => {
    return get().errors["pipeline"];
  },
})


/**
 * Updates the class names of edges in a pipeline based on error status.
 *
 * @remarks
 * This function modifies the edge classes to visually indicate error states. Edges with errors
 * receive an "edge-error" class, while error-free edges have their class name removed.
 *
 * @param pipeline - The pipeline containing edges to be checked
 * @param errors - An object containing error information for different pipeline components
 *
 * @returns Void. Modifies edges in-place by adding or removing "edge-error" class.
 */
function updateEdgeClasses(pipeline: PipelineType, errors: ErrorsType) {
  if (!pipeline.data) {
    return;
  }
  const edgeErrors = errors["edge"] || [];
  for (const edge of pipeline.data!.edges) {
    if (edgeErrors.includes(edge["id"])) {
      edge["className"] = "edge-error";
    } else {
      delete edge["className"];
    }
  }
}
