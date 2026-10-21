import {afterAll, beforeAll, beforeEach, describe, expect, it, vi} from "vitest";
import {fireEvent, render, screen} from "@testing-library/react";
import {Node} from "reactflow";
import FindPanel from "./FindPanel";
import usePipelineStore from "../stores/pipelineStore";

const SCHEMA_SCRIPTS: Record<string, unknown> = {
  "parameter-values": {},
  "default-values": {},
  "node-schemas": [
    {
      title: "LLMResponseWithPrompt",
      "ui:label": "LLM",
      properties: {name: {type: "string"}, prompt: {type: "string", title: "Prompt", "ui:widget": "text_editor_widget"}},
    },
    {
      title: "RouterNode",
      "ui:label": "LLM Router",
      properties: {name: {type: "string"}, keywords: {type: "array", title: "Outputs", "ui:widget": "keywords"}},
    },
  ],
  "flags-enabled": [],
  "llm-model-params": {},
  "llm-model-parameter-schemas": {},
};

const nodes: Node[] = [
  {
    id: "llm-1",
    type: "pipelineNode",
    position: {x: 0, y: 0},
    data: {type: "LLMResponseWithPrompt", params: {name: "Greeter", prompt: "Read billing from temp_state"}},
  },
  {
    id: "router-1",
    type: "pipelineNode",
    position: {x: 100, y: 0},
    data: {type: "RouterNode", params: {name: "Triage", keywords: ["BILLING", "OTHER"]}},
  },
];

const focusNode = vi.fn();
const originalFocusNode = usePipelineStore.getState().focusNode;

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
  usePipelineStore.setState({focusNode: originalFocusNode, readOnly: false});
});

beforeEach(() => {
  focusNode.mockClear();
  usePipelineStore.getState().resetFlow({nodes, edges: []});
  usePipelineStore.setState({focusNode, readOnly: false});
});

function openPanel(setIsOpen = vi.fn()) {
  render(<FindPanel isOpen={true} setIsOpen={setIsOpen} />);
  return screen.getByPlaceholderText("Find in pipeline");
}

describe("FindPanel", () => {
  it("renders no search input while closed", () => {
    render(<FindPanel isOpen={false} setIsOpen={vi.fn()} />);

    expect(screen.queryByPlaceholderText("Find in pipeline")).toBeNull();
  });

  it("toggles from the trigger button", () => {
    const setIsOpen = vi.fn();
    render(<FindPanel isOpen={false} setIsOpen={setIsOpen} />);

    fireEvent.click(screen.getByTitle("Find in pipeline"));

    expect(setIsOpen).toHaveBeenCalledWith(true);
  });

  it("lists matches grouped by node with a count", () => {
    const input = openPanel();

    fireEvent.change(input, {target: {value: "billing"}});

    expect(screen.getByText("2 matches in 2 nodes")).toBeInTheDocument();
    expect(screen.getByText("Greeter")).toBeInTheDocument();
    expect(screen.getByText("Triage")).toBeInTheDocument();
    expect(screen.getAllByRole("option").map((row) => row.textContent)).toEqual([
      "PromptRead billing from temp_state",
      "OutputsBILLING",
    ]);
    expect(screen.getByText("billing", {selector: "mark"})).toBeInTheDocument();
  });

  it("says so when nothing matches", () => {
    const input = openPanel();

    fireEvent.change(input, {target: {value: "nothing here"}});

    expect(screen.getByText("No matches")).toBeInTheDocument();
  });

  it("focuses the node of a clicked result", () => {
    const input = openPanel();
    fireEvent.change(input, {target: {value: "billing"}});

    fireEvent.click(screen.getAllByRole("option")[1]);

    expect(focusNode).toHaveBeenCalledWith("router-1", "keywords");
  });

  it("moves through results with the arrow keys and activates one with Enter", () => {
    const input = openPanel();
    fireEvent.change(input, {target: {value: "billing"}});

    fireEvent.keyDown(input, {key: "Enter"});
    fireEvent.keyDown(input, {key: "ArrowDown"});
    fireEvent.keyDown(input, {key: "Enter"});
    fireEvent.keyDown(input, {key: "ArrowDown"});
    fireEvent.keyDown(input, {key: "ArrowUp"});
    fireEvent.keyDown(input, {key: "Enter"});

    expect(focusNode.mock.calls).toEqual([["llm-1", "prompt"], ["router-1", "keywords"], ["llm-1", "prompt"]]);
  });

  it("names no field for a match on the node id", () => {
    const input = openPanel();
    fireEvent.change(input, {target: {value: "router-1"}});

    fireEvent.keyDown(input, {key: "Enter"});

    expect(focusNode).toHaveBeenCalledWith("router-1", undefined);
  });

  it("closes on Escape", () => {
    const setIsOpen = vi.fn();
    const input = openPanel(setIsOpen);

    fireEvent.keyDown(input, {key: "Escape"});

    expect(setIsOpen).toHaveBeenCalledWith(false);
  });
});
