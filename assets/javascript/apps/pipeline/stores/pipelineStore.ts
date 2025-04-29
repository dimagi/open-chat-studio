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
import {isEqual} from "lodash";

let saveTimeoutId: NodeJS.Timeout | null = null;

const createPipelineStore: StateCreator<
  PipelineStoreType & PipelineManagerStoreType,
  [],
  [],
  PipelineStoreType
> = (set, get) => ({
  nodes: [],
  edges: [],
  readOnly: false,
  reactFlowInstance: null,
  setReactFlowInstance: (newState) => {
    set({reactFlowInstance: newState});
    if (get().currentPipeline) {
      get().resetFlow({
        nodes: get().currentPipeline?.data?.nodes ?? [],
        edges: get().currentPipeline?.data?.edges ?? [],
      });
    }
  },
  setReadOnly: (value: boolean) => set({ readOnly: value }),
  onNodesChange: (changes: NodeChange[]) => {
    set({
      nodes: applyNodeChanges(changes, get().nodes),
    });
  },
  onEdgesChange: (changes: EdgeChange[]) => {
    if (get().readOnly) return;
    set({
      edges: applyEdgeChanges(changes, get().edges),
    });
  },
  setNodes: (change) => {
    if (get().readOnly) return;
    const newChange = typeof change === "function" ? change(get().nodes) : change;
    const newEdges = get().edges;

    set({
      edges: newEdges,
      nodes: newChange,
    });

    get().autoSaveCurrentPipline(
      newChange,
      newEdges,
    );
  },
  setEdges: (change) => {
    if (get().readOnly) return;
    const newChange = typeof change === "function" ? change(get().edges) : change;

    set({
      edges: newChange,
    });

    get().autoSaveCurrentPipline(
      get().nodes,
      newChange,
    );
  },
  setNode: (id: string, change: Node | ((oldState: Node) => Node)) => {
    if (get().readOnly) return;
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
    if (get().readOnly) return;
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
    if (get().readOnly) return;
    get().setEdges(
      get().edges.filter((edge) =>
        typeof edgeId === "string"
          ? edge.id !== edgeId
          : !edgeId.includes(edge.id)
      )
    );
  },
  clearEdgeLabels: () => {
    if (get().readOnly) return;
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
    if (get().readOnly) return;
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
    if (get().readOnly) return;
    let newEdges: Edge[] = [];
    get().setEdges((oldEdges) => {
      newEdges = addEdge(connection, oldEdges);
      return newEdges;
    });
    get().autoSaveCurrentPipline(
        get().nodes,
        newEdges,
      );
  },
  addNode: (node, position) => {
    if (get().readOnly) return;
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

const createPipelineManagerStore: StateCreator<
  PipelineManagerStoreType & PipelineStoreType,
  [],
  [],
  PipelineManagerStoreType
> = (set, get) => ({
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
        if (pipeline.data) {
          updateEdgeClasses(pipeline.data.edges, pipeline.errors);
        }
        set({currentPipeline: pipeline, currentPipelineId: pipelineId});
        set({errors: pipeline.errors});
        set({isLoading: false});
        if (get().reactFlowInstance) {
          get().resetFlow({
            nodes: pipeline?.data?.nodes ?? [],
            edges: pipeline?.data?.edges ?? [],
          });
        }
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
    if (!get().currentPipeline) {
      return
    }
    const dataToSave = getDataToSave(
      {
        nodes: get().currentPipeline!.data?.nodes || [],
        edges: get().currentPipeline!.data?.edges || [],
      },
      {nodes, edges}
    )
    if (!dataToSave) {
      return;
    }
    set({dirty: true});
    // Clear the previous timeout if it exists.
    if (saveTimeoutId) {
      clearTimeout(saveTimeoutId);
    }

    // Set up a new timeout.
    saveTimeoutId = setTimeout(() => {
      if (get().currentPipeline) {
        get().savePipeline(
          {...get().currentPipeline!, data: dataToSave},
          true,
        );
      }
    }, 1000);
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
            set({currentPipeline: pipeline, dirty: false});
            set({errors: response.errors});
            if (get().reactFlowInstance && response.errors) {
              set({
                edges: updateEdgeClasses(get().edges, response.errors)
              })
            }
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


const usePipelineStore = create<PipelineStoreType & PipelineManagerStoreType>((...a) => ({
  ...createPipelineStore(...a),
  ...createPipelineManagerStore(...a),
}));

export default usePipelineStore;


/**
 * Updates the class names of edges in a pipeline based on error status.
 *
 * @remarks
 * This function modifies the edge classes to visually indicate error states. Edges with errors
 * receive an "edge-error" class, while error-free edges have their class name removed.
 *
 * @param edges - List of edges
 * @param errors - An object containing error information for different pipeline components
 *
 * @returns Void. Modifies edges in-place by adding or removing "edge-error" class.
 */
function updateEdgeClasses(edges: Edge[], errors: ErrorsType) {
  const edgeErrors = errors["edge"] || [];
  for (const edge of edges) {
    if (edgeErrors.includes(edge["id"])) {
      edge["className"] = "edge-error";
    } else {
      delete edge["className"];
    }
  }
  return edges;
}

interface NodesAndEdges {
  nodes: Node[],
  edges: Edge[],
}

/**
 * Returns the data to save if it is different from the data that was saved before.
 *
 * @param oldPipelineData - The data that was saved before
 * @param newPipelineData - The data that is currently in the editor
 *
 * @returns The data to save if it is different from the data that was saved before, otherwise undefined
 */
const getDataToSave = (oldPipelineData: NodesAndEdges, newPipelineData: NodesAndEdges) => {
  // See `apps.pipelines.flow.FlowNode
  const newNodes = byId(newPipelineData.nodes.map(({id, position, type, data}) => ({id, position, type, data})));
  const oldNodes = byId(oldPipelineData.nodes);
  if (!isEqual(oldNodes, newNodes)) {
    return newPipelineData;
  }

  // See `apps.pipelines.flow.FlowEdge`
  const newEdges = byId(newPipelineData.edges.map(({id, source, target, sourceHandle, targetHandle}) => ({
        id,
        source,
        target,
        sourceHandle,
        targetHandle
      })));
  const oldEdges = byId(oldPipelineData.edges);
  if (!isEqual(oldEdges, newEdges)) {
    return newPipelineData;
  }
  return undefined;
}

/**
 * Returns an object with the given array of objects as values, with the `id` field as the key.
 * @param arr
 */
const byId = <T extends {id: string}>(arr: T[]) => {
  return arr.reduce((acc, node) => {
      acc[node.id] = node;
      return acc;
    }, {} as {[key: string]: T});
}
