import {afterEach, beforeEach, describe, expect, test, vi} from "vitest";
import {Edge, Node} from "reactflow";
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

function seed() {
  // readOnly is reset explicitly: nothing else in this file resets it after a test sets it,
  // so without this a read-only test leaks readOnly: true into whatever runs next.
  usePipelineStore.setState({readOnly: false});
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

describe("pipelineStore undo/redo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    seed();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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
  const plainData: NodeData = {type: "StaticRouterNode", label: "Router", params: {name: "b", keywords: ["a"]}};
  const routerData: NodeData = {type: "StaticRouterNode", label: "Router", params: {name: "a"}};

  beforeEach(() => {
    vi.useFakeTimers();
    seed();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("replaces the node with a fresh id at the same position, carrying the new data", () => {
    usePipelineStore.getState().changeNodeType("b", plainData);
    flushThrottle();

    const remaining = usePipelineStore.getState().nodes.filter((n) => n.id !== "a");
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).not.toBe("b");
    expect(remaining[0].position).toEqual(nodeB.position);
    expect(remaining[0].data).toEqual(plainData);
  });

  test("reconnects the incoming edge to the new node", () => {
    usePipelineStore.getState().changeNodeType("b", plainData);
    flushThrottle();

    const newId = usePipelineStore.getState().nodes.find((n) => n.id !== "a")!.id;
    expect(usePipelineStore.getState().edges).toEqual([
      expect.objectContaining({source: "a", target: newId}),
    ]);
  });

  test("reconnects an outgoing edge to the new node's single output handle", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [{...nodeA, data: routerData}, nodeB],
      edges: [{id: "a-b", source: "a", target: "b", sourceHandle: "output_0"}],
    });

    usePipelineStore.getState().changeNodeType("a", {type: "LLMResponse", label: "LLM", params: {name: "a"}});
    flushThrottle();

    const newId = usePipelineStore.getState().nodes.find((n) => n.id !== "b")!.id;
    expect(usePipelineStore.getState().edges).toEqual([
      expect.objectContaining({source: newId, target: "b", sourceHandle: "output"}),
    ]);
  });

  test("reconnects an outgoing edge to output_0 when the new type has multiple outputs", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [{...nodeA, data: {type: "LLMResponse", label: "LLM", params: {name: "a"}}}, nodeB],
      edges: [{id: "a-b", source: "a", target: "b", sourceHandle: "output"}],
    });

    usePipelineStore.getState().changeNodeType("a", routerData);
    flushThrottle();

    const newId = usePipelineStore.getState().nodes.find((n) => n.id !== "b")!.id;
    expect(usePipelineStore.getState().edges).toEqual([
      expect.objectContaining({source: newId, target: "b", sourceHandle: "output_0"}),
    ]);
  });

  // #1452: a router with more than one wired branch keeps only its first outgoing connection --
  // the same as every type change already does today for every branch past the first.
  test("drops every outgoing edge but the first when the old node had more than one", () => {
    usePipelineStore.getState().resetFlow({
      nodes: [{...nodeA, data: routerData}, nodeB, {id: "c", type: "pipelineNode", position: {x: 200, y: 0}, data: {type: "LLMResponse", params: {}}}],
      edges: [
        {id: "a-b", source: "a", target: "b", sourceHandle: "output_0"},
        {id: "a-c", source: "a", target: "c", sourceHandle: "output_1"},
      ],
    });

    usePipelineStore.getState().changeNodeType("a", {type: "LLMResponse", label: "LLM", params: {name: "a"}});
    flushThrottle();

    const newId = usePipelineStore.getState().nodes.find((n) => n.id !== "b" && n.id !== "c")!.id;
    expect(usePipelineStore.getState().edges).toEqual([
      expect.objectContaining({source: newId, target: "b"}),
    ]);
  });

  test("closes the editor, same as deleteNode", () => {
    useEditorStore.getState().openEditorForNode({id: "b", data: nodeB.data} as never);

    usePipelineStore.getState().changeNodeType("b", plainData);
    flushThrottle();

    expect(useEditorStore.getState().currentNode).toBeNull();
  });

  test("undo restores the original node and edge in one step", () => {
    usePipelineStore.getState().changeNodeType("b", plainData);
    flushThrottle();

    usePipelineStore.temporal.getState().undo();

    expect(usePipelineStore.getState().nodes).toEqual(
      expect.arrayContaining([expect.objectContaining({id: "b", data: nodeB.data})]),
    );
    expect(usePipelineStore.getState().edges).toEqual([edgeAB]);
  });

  test("does nothing in read-only mode", () => {
    usePipelineStore.setState({readOnly: true});

    usePipelineStore.getState().changeNodeType("b", plainData);

    expect(usePipelineStore.getState().nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
  });
});
