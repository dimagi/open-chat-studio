import {afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi} from "vitest";
import {act, render} from "@testing-library/react";
import {Node, NodeProps} from "reactflow";
import EditPanel from "./EditPanel";
import usePipelineStore from "../stores/pipelineStore";
import useEditorStore from "../stores/editorStore";
import {NodeData} from "../types/nodeParams";

const SCHEMA_SCRIPTS: Record<string, unknown> = {
  "parameter-values": {},
  "default-values": {},
  "node-schemas": [
    {title: "Passthrough", "ui:label": "Do Nothing", properties: {name: {type: "string"}, tag: {type: "string"}}},
  ],
  "flags-enabled": [],
  "llm-model-params": {},
  "llm-model-parameter-schemas": {},
};

const node: Node<NodeData> = {
  id: "n1",
  type: "pipelineNode",
  position: {x: 0, y: 0},
  data: {type: "Passthrough", label: "Do Nothing", params: {name: "n1", tag: "billing"}},
};

// getCachedData() reads these script tags once and caches the result for the lifetime of the module.
beforeAll(() => {
  for (const [id, data] of Object.entries(SCHEMA_SCRIPTS)) {
    const el = document.createElement("script");
    el.type = "application/json";
    el.id = id;
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
  }
});

afterAll(() => {
  for (const id of Object.keys(SCHEMA_SCRIPTS)) {
    document.getElementById(id)?.remove();
  }
});

beforeEach(() => {
  vi.useFakeTimers();
  usePipelineStore.getState().resetFlow({nodes: [node], edges: []});
});

afterEach(() => {
  useEditorStore.getState().closeEditor();
  vi.useRealTimers();
});

function openEditor(field?: string) {
  useEditorStore.getState().openEditorForNode({id: node.id, data: node.data} as NodeProps<NodeData>, field);
}

describe("EditPanel", () => {
  it("highlights the requested field, then clears the highlight", () => {
    openEditor("tag");
    const {container} = render(<EditPanel nodeId="n1" />);
    const field = container.querySelector('[name="tag"]')!;

    expect(field).toHaveClass("ring-2");
    act(() => vi.advanceTimersByTime(1500));
    expect(field).not.toHaveClass("ring-2");
  });

  it("highlights nothing when no field is requested", () => {
    openEditor();
    const {container} = render(<EditPanel nodeId="n1" />);

    expect(container.querySelector(".ring-2")).toBeNull();
  });

  it("ignores a field the editor does not show", () => {
    openEditor("missing");

    expect(() => render(<EditPanel nodeId="n1" />)).not.toThrow();
  });
});
