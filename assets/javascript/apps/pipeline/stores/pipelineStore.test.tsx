import {afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi} from "vitest";
import {Edge, Node, NodeProps} from "reactflow";
import usePipelineStore, {withTemporalPaused} from "./pipelineStore";
import useEditorStore from "./editorStore";
import {NodeData} from "../types/nodeParams";

const nodeA: Node = {id: "a", type: "pipelineNode", position: {x: 0, y: 0}, data: {type: "LLMResponse", params: {}}};
const nodeB: Node = {id: "b", type: "pipelineNode", position: {x: 100, y: 0}, data: {type: "LLMResponse", params: {}}};
const edgeAB: Edge = {id: "a-b", source: "a", target: "b"};

// handleSet is throttled (see pipelineStore.ts) so a node drag collapses into one history
// entry instead of one per pointer move. Fake timers make that deterministic here: without
// them, the throttle's real wall-clock window from one test can still be open when the next
// test's assertion runs, since the throttled function is a single instance shared for the
// life of the store.
function flushThrottle() {
  vi.advanceTimersByTime(500);
}

// The store is a singleton that nothing resets between tests, so every test that sets one of
// these leaks it into whatever runs next.
function resetStore() {
  usePipelineStore.setState({readOnly: false, currentPipeline: undefined, dirty: false});
}

function seed() {
  resetStore();
  usePipelineStore.getState().resetFlow({nodes: [nodeA, nodeB], edges: [edgeAB]});
  usePipelineStore.temporal.getState().clear();
}

// Autosave only fires once a pipeline is loaded (autoSaveCurrentPipline early-returns
// without one), so the undo/redo-triggers-autosave tests need this seeded too.
function seedCurrentPipeline() {
  usePipelineStore.setState({
    currentPipeline: {
      id: BigInt(1),
      team: "test-team",
      name: "Test Pipeline",
      data: {nodes: [nodeA, nodeB], edges: [edgeAB]},
      description: "",
      errors: {},
    },
    currentPipelineId: 1,
    currentRevision: 0,
  });
}

// changeNodeType reads the node schemas through getCachedData(), which pulls them out of these
// script tags once and caches the result for the lifetime of the module.
const SCHEMA_SCRIPTS: Record<string, unknown> = {
  "parameter-values": {},
  "default-values": {},
  "node-schemas": [
    {
      title: "LLMResponse",
      "ui:label": "LLM",
      "ui:flow_node_type": "pipelineNode",
      "ui:can_add": true,
      properties: {prompt: {type: "string", default: "Say hi"}},
    },
    {
      title: "AssistantNode",
      "ui:label": "Assistant",
      "ui:flow_node_type": "pipelineNode",
      "ui:can_add": true,
      properties: {},
    },
    {
      title: "RouterNode",
      "ui:label": "Router",
      "ui:flow_node_type": "pipelineNode",
      "ui:can_add": true,
      properties: {keywords: {type: "array"}},
    },
  ],
  "flags-enabled": [],
  "llm-model-params": {},
  "llm-model-parameter-schemas": {},
};

// `alertify` is a global the Django templates load, so the store reaches for it without an
// import and it is simply absent here.
const alertifyWarning = vi.fn();

beforeAll(() => {
  vi.stubGlobal("alertify", {error: vi.fn(), success: vi.fn(), warning: alertifyWarning});
  for (const [id, data] of Object.entries(SCHEMA_SCRIPTS)) {
    const el = document.createElement("script");
    el.type = "application/json";
    el.id = id;
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
  }
});

afterAll(() => {
  vi.unstubAllGlobals();
  for (const id of Object.keys(SCHEMA_SCRIPTS)) {
    document.getElementById(id)?.remove();
  }
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  // Drain the throttled history handler before handing the clock back. The throttle is one
  // instance shared for the life of the store, so a pending trailing call would be counted
  // against the next test's history. Clearing the timers then drops the autosave a mutation
  // leaves behind, which would otherwise fire on a later test's clock and reach the network.
  flushThrottle();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("pipelineStore undo/redo", () => {
  beforeEach(seed);

  test("undo restores a deleted node and its connected edge", () => {
    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    expect(usePipelineStore.getState().nodes.map((n) => n.id)).toEqual(["a"]);
    expect(usePipelineStore.getState().edges).toEqual([]);

    usePipelineStore.temporal.getState().undo();

    expect(usePipelineStore.getState().nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
    expect(usePipelineStore.getState().edges).toEqual([edgeAB]);
  });

  test("redo re-applies an undone node deletion", () => {
    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    usePipelineStore.temporal.getState().undo();
    usePipelineStore.temporal.getState().redo();

    expect(usePipelineStore.getState().nodes.map((n) => n.id)).toEqual(["a"]);
    expect(usePipelineStore.getState().edges).toEqual([]);
  });

  test("resetFlow does not create an undo step", () => {
    usePipelineStore.getState().resetFlow({nodes: [nodeA], edges: []});
    usePipelineStore.getState().resetFlow({nodes: [nodeA, nodeB], edges: [edgeAB]});
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(0);
  });

  test("undo history only grows from the user edit between two resets, not the resets themselves", () => {
    usePipelineStore.getState().deleteNode("b");
    usePipelineStore.getState().resetFlow({nodes: [nodeA], edges: []});
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(1);
  });

  // React Flow fires onNodesChange/onEdgesChange for its own bookkeeping, not just user
  // edits: a 'dimensions' change on every node as it first measures itself, and a 'select'
  // change on every click, including a click that only selects a node without moving it.
  // Neither is something a user would expect Ctrl+Z to walk back through.
  test("a node dimensions/select-only change does not create an undo step", () => {
    usePipelineStore.getState().onNodesChange([
      {id: "a", type: "dimensions", dimensions: {width: 200, height: 80}},
    ]);
    usePipelineStore.getState().onNodesChange([{id: "a", type: "select", selected: true}]);
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(0);
  });

  test("an edge select-only change does not create an undo step", () => {
    usePipelineStore.getState().onEdgesChange([{id: "a-b", type: "select", selected: true}]);
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(0);
  });

  // zundo pushes a history entry on every set() call by default, whether or not the
  // partialized (nodes/edges) slice actually changed. Without an equality function, a
  // set() call that touches neither key — isLoading here, but the same is true of
  // loadPipeline's early currentPipeline/currentRevision writes — would still count as an
  // undo step.
  test("a set() call that touches neither nodes nor edges does not create an undo step", () => {
    usePipelineStore.getState().setIsLoading(true);
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(0);
  });

  // React Flow sends a 'position' change with no `position` field, just `dragging: false`,
  // on a plain click that never moved the node — its own drag-end bookkeeping, not a move.
  test("a position change with no position value (click, no drag) does not create an undo step", () => {
    usePipelineStore.getState().onNodesChange([{id: "a", type: "position", dragging: false}]);
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(0);
  });

  test("a real node position change is still trackable", () => {
    usePipelineStore.getState().onNodesChange([
      {id: "a", type: "position", position: {x: 50, y: 50}, dragging: false},
    ]);
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(1);
    usePipelineStore.temporal.getState().undo();
    expect(usePipelineStore.getState().nodes.find((n) => n.id === "a")?.position).toEqual({x: 0, y: 0});
  });

  // zundo's undo()/redo() call the store's raw set() directly, bypassing setNodes/setEdges —
  // the only places that call autoSaveCurrentPipline(). Without an explicit trigger, undoing
  // a change restores the canvas but never tells the server, so a reload right after would
  // lose it.
  test("undo triggers an autosave", () => {
    seedCurrentPipeline();

    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    // deleteNode's own autoSaveCurrentPipline() call already set this; reset it so the
    // assertion below isolates undo's own trigger, not delete's.
    usePipelineStore.setState({dirty: false});

    usePipelineStore.getState().undoLastChange();

    expect(usePipelineStore.getState().dirty).toBe(true);
  });

  test("redo triggers an autosave", () => {
    seedCurrentPipeline();

    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    usePipelineStore.getState().undoLastChange();
    usePipelineStore.setState({dirty: false});

    usePipelineStore.getState().redoLastChange();

    expect(usePipelineStore.getState().dirty).toBe(true);
  });

  // Every other mutator in this store (setNodes, deleteNode, onConnect, ...) guards itself
  // with `if (get().readOnly) return`, not just relying on the UI to check first. These two
  // should match that, not just the UI-level check in Pipeline.tsx.
  test("undoLastChange does nothing in read-only mode", () => {
    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    usePipelineStore.setState({readOnly: true});

    usePipelineStore.getState().undoLastChange();

    expect(usePipelineStore.getState().nodes.map((n) => n.id)).toEqual(["a"]);
  });

  // withTemporalPaused brackets every server-driven or React-Flow-bookkeeping write with
  // pause()/resume(). Without a try/finally around the callback, a throw would leave tracking
  // paused for the rest of the session, silently dropping every undo step after.
  test("withTemporalPaused resumes tracking even when the callback throws", () => {
    expect(() => {
      withTemporalPaused(true, () => {
        throw new Error("boom");
      });
    }).toThrow("boom");

    expect(usePipelineStore.temporal.getState().isTracking).toBe(true);
  });

  test("redoLastChange does nothing in read-only mode", () => {
    usePipelineStore.getState().deleteNode("b");
    flushThrottle();
    usePipelineStore.setState({readOnly: false});
    usePipelineStore.getState().undoLastChange();
    usePipelineStore.setState({readOnly: true});

    usePipelineStore.getState().redoLastChange();

    expect(usePipelineStore.getState().nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
  });
});


describe("pipelineStore changeNodeType", () => {
  const nodeC: Node = {id: "c", type: "pipelineNode", position: {x: 200, y: 0}, data: {type: "LLMResponse", params: {}}};
  const nodeD: Node = {id: "d", type: "pipelineNode", position: {x: 200, y: 100}, data: {type: "LLMResponse", params: {}}};
  const chainAB: Edge = {id: "a-b", source: "a", sourceHandle: "output", target: "b", targetHandle: "input"};
  const chainBC: Edge = {id: "b-c", source: "b", sourceHandle: "output", target: "c", targetHandle: "input"};

  // a -> b -> c, so b has exactly one edge on each side.
  function seedChain(middle: Node = nodeB, outgoingHandle = "output") {
    resetStore();
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, middle, nodeC],
      edges: [chainAB, {...chainBC, sourceHandle: outgoingHandle}],
    });
    usePipelineStore.temporal.getState().clear();
  }

  function replacementNode() {
    return usePipelineStore.getState().nodes.find((node) => !["a", "b", "c", "d"].includes(node.id));
  }

  // The editor store is a singleton too, so an open editor leaks into the next test.
  function openEditorOn(node: Node) {
    useEditorStore.getState().openEditorForNode({id: node.id, data: node.data} as NodeProps<NodeData>);
  }

  beforeEach(() => {
    seedChain();
    alertifyWarning.mockClear();
    useEditorStore.getState().closeEditor();
  });

  test("swaps the node for one of the chosen type, in the same place and with that type's defaults", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    const nodes = usePipelineStore.getState().nodes;
    expect(nodes.map((node) => node.id)).not.toContain("b");
    const replacement = replacementNode()!;
    expect(replacement.data.type).toBe("AssistantNode");
    expect(replacement.data.label).toBe("Assistant");
    expect(replacement.position).toEqual(nodeB.position);
    expect(replacement.data.params.name).toBe(replacement.id);
  });

  test("the replacement starts from the new type's default params", () => {
    seedChain({...nodeB, data: {type: "AssistantNode", params: {}}});

    usePipelineStore.getState().changeNodeType("b", "LLMResponse");

    expect(replacementNode()!.data.params.prompt).toBe("Say hi");
  });

  test("the replacement keeps the old node's colour", () => {
    seedChain({...nodeB, data: {type: "LLMResponse", params: {color: "bg-red-100 dark:bg-red-950"}}});

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(replacementNode()!.data.params.color).toBe("bg-red-100 dark:bg-red-950");
  });

  test("rewires the incoming edge and the single outgoing edge onto the replacement", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    const newId = replacementNode()!.id;
    const edges = usePipelineStore.getState().edges;
    expect(edges).toHaveLength(2);
    const incoming = edges.find((edge) => edge.source === "a")!;
    expect(incoming.target).toBe(newId);
    expect(incoming.targetHandle).toBe("input");
    expect(edges.find((edge) => edge.target === "c")!.source).toBe(newId);
  });

  // A router's handles are output_0, output_1, ...; it has no plain "output" to inherit.
  test.each([
    {from: "LLMResponse", on: "output", to: "AssistantNode", lands: "output"},
    {from: "LLMResponse", on: "output", to: "RouterNode", lands: "output_0"},
    {from: "RouterNode", on: "output_2", to: "AssistantNode", lands: "output"},
  ])("the single outgoing edge off a $from's $on lands on a $to's $lands", ({from, on, to, lands}) => {
    seedChain({...nodeB, data: {type: from, params: {}}}, on);

    usePipelineStore.getState().changeNodeType("b", to);

    const outgoing = usePipelineStore.getState().edges.find((edge) => edge.target === "c")!;
    expect(outgoing.source).toBe(replacementNode()!.id);
    expect(outgoing.sourceHandle).toBe(lands);
  });

  test("drops every outgoing edge when the old node had more than one", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, {...nodeB, data: {type: "RouterNode", params: {}}}, nodeC, nodeD],
      edges: [
        chainAB,
        {...chainBC, sourceHandle: "output_0"},
        {id: "b-d", source: "b", sourceHandle: "output_1", target: "d", targetHandle: "input"},
      ],
    });

    usePipelineStore.getState().changeNodeType("b", "LLMResponse");

    const edges = usePipelineStore.getState().edges;
    expect(edges).toHaveLength(1);
    expect(edges[0].target).toBe(replacementNode()!.id);
  });

  test("keeps edges that never touched the node", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, nodeB, nodeC, nodeD],
      edges: [chainAB, chainBC, {id: "c-d", source: "c", sourceHandle: "output", target: "d", targetHandle: "input"}],
    });

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(usePipelineStore.getState().edges.find((edge) => edge.id === "c-d")).toEqual({
      id: "c-d", source: "c", sourceHandle: "output", target: "d", targetHandle: "input",
    });
  });

  test("the whole swap is a single undo step", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");
    flushThrottle();

    expect(usePipelineStore.temporal.getState().pastStates).toHaveLength(1);

    usePipelineStore.temporal.getState().undo();

    expect(usePipelineStore.getState().nodes.map((node) => node.id).sort()).toEqual(["a", "b", "c"]);
    expect(usePipelineStore.getState().edges).toEqual([chainAB, chainBC]);
  });

  test("redo re-applies an undone swap", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");
    flushThrottle();
    const newId = replacementNode()!.id;
    usePipelineStore.temporal.getState().undo();

    usePipelineStore.temporal.getState().redo();

    expect(usePipelineStore.getState().nodes.map((node) => node.id).sort()).toEqual(["a", "c", newId].sort());
  });

  test("triggers an autosave", () => {
    seedCurrentPipeline();

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(usePipelineStore.getState().dirty).toBe(true);
  });

  test("rewires every incoming edge, giving each a distinct id", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, nodeB, nodeC, nodeD],
      edges: [
        chainAB,
        {id: "d-b", source: "d", sourceHandle: "output", target: "b", targetHandle: "input"},
      ],
    });

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    const newId = replacementNode()!.id;
    const edges = usePipelineStore.getState().edges;
    expect(edges).toHaveLength(2);
    expect(edges.map((edge) => edge.target)).toEqual([newId, newId]);
    expect(edges.map((edge) => edge.source).sort()).toEqual(["a", "d"]);
    expect(new Set(edges.map((edge) => edge.id)).size).toBe(2);
  });

  // Both ends move to the new node, so the loop would have to be rewired onto a handle chosen
  // for it rather than one the user picked.
  test("drops a self-loop rather than rewiring it", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, nodeB],
      edges: [chainAB, {id: "b-b", source: "b", sourceHandle: "output", target: "b", targetHandle: "input"}],
    });

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    const edges = usePipelineStore.getState().edges;
    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe("a");
  });

  // setNode's function form asserts the node is still there, so an editor left open on the
  // removed node would throw on the next keystroke.
  test("closes the editor when it is open on the node being replaced", () => {
    openEditorOn(nodeB);

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(useEditorStore.getState().currentNode).toBeNull();
  });

  test("leaves the editor alone when it is open on a different node", () => {
    openEditorOn(nodeA);

    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(useEditorStore.getState().currentNode?.id).toBe("a");
  });

  test("selects the replacement so the toolbar stays on screen", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(replacementNode()!.selected).toBe(true);
    expect(usePipelineStore.getState().nodes.filter((node) => node.selected)).toHaveLength(1);
  });

  test("leaves no color key on the replacement when the old node had no colour", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(replacementNode()!.data.params).not.toHaveProperty("color");
  });

  test.each([
    {what: "a self-loop", edges: [{id: "b-b", source: "b", sourceHandle: "output", target: "b", targetHandle: "input"}], dropped: 1},
    {
      what: "two outgoing edges",
      edges: [
        {id: "b-c", source: "b", sourceHandle: "output_0", target: "c", targetHandle: "input"},
        {id: "b-d", source: "b", sourceHandle: "output_1", target: "d", targetHandle: "input"},
      ],
      dropped: 2,
    },
  ])("warns the user when the swap drops $what", ({edges, dropped}) => {
    usePipelineStore.getState().resetFlow({
      nodes: [nodeA, {...nodeB, data: {type: "RouterNode", params: {}}}, nodeC, nodeD],
      edges: [chainAB, ...edges],
    });

    usePipelineStore.getState().changeNodeType("b", "LLMResponse");

    expect(alertifyWarning).toHaveBeenCalledOnce();
    expect(alertifyWarning.mock.calls[0][0]).toContain(`${dropped} connection`);
  });

  test("does not warn when every edge survives the swap", () => {
    usePipelineStore.getState().changeNodeType("b", "AssistantNode");

    expect(alertifyWarning).not.toHaveBeenCalled();
  });

  // A pipeline can hold a node whose type the server no longer serves a schema for, so an
  // unknown target has to be a no-op rather than a throw.
  test.each([
    {guard: "read-only mode", arrange: () => usePipelineStore.setState({readOnly: true}), type: "AssistantNode"},
    {guard: "a target type with no schema", arrange: () => {}, type: "NoSuchNode"},
    {guard: "a node that is already that type", arrange: () => {}, type: "LLMResponse"},
  ])("does nothing for $guard", ({arrange, type}) => {
    arrange();

    usePipelineStore.getState().changeNodeType("b", type);

    expect(usePipelineStore.getState().nodes.map((node) => node.id)).toEqual(["a", "b", "c"]);
  });
});
