import {afterEach, beforeEach, describe, expect, test, vi} from "vitest";
import {Edge, Node} from "reactflow";
import usePipelineStore from "./pipelineStore";

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
  usePipelineStore.getState().resetFlow({nodes: [nodeA, nodeB], edges: [edgeAB]});
  usePipelineStore.temporal.getState().clear();
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
});
