import {beforeAll, beforeEach, describe, expect, it, vi} from "vitest";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {apiClient} from "../api/api";
import {TextEditorWidget} from "./widgets";
import type {WidgetParams} from "./widgets";
import type {JsonSchema, NodeParams} from "../types/nodeParams";
import type {ToolCompletion} from "./toolNames";

// The real PromptEditor mounts CodeMirror, which is not reliable in jsdom. The stub records the
// tool list it is given on each render.
const receivedTools: ToolCompletion[][] = [];
vi.mock("../components/CodeMirrorEditor", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../components/CodeMirrorEditor")>()),
  PromptEditor: ({toolCompletions}: {toolCompletions?: ToolCompletion[]}) => {
    receivedTools.push(toolCompletions ?? []);
    return null;
  },
}));

// getCachedData() reads these script tags once and caches the result for the lifetime of the module.
beforeAll(() => {
  const data: Record<string, unknown> = {
    "parameter-values": {tools: [{value: "one-off-reminder", label: "One-off Reminder"}], llm_prompt_variables: []},
    "default-values": {},
    "node-schemas": [],
    "flags-enabled": [],
    "llm-model-params": {},
    "llm-model-parameter-schemas": {},
  };
  for (const [id, value] of Object.entries(data)) {
    const el = document.createElement("script");
    el.type = "application/json";
    el.id = id;
    el.textContent = JSON.stringify(value);
    document.body.appendChild(el);
  }
});

beforeEach(() => {
  receivedTools.length = 0;
});

const llmSchema = {title: "LLMResponseWithPrompt", properties: {prompt: {type: "string"}, tools: {type: "array"}}} as unknown as JsonSchema;
const routerSchema = {title: "RouterNode", properties: {prompt: {type: "string"}}} as unknown as JsonSchema;

function props(nodeParams: NodeParams, nodeSchema = llmSchema): WidgetParams {
  return {
    nodeId: "node-1",
    name: "prompt",
    label: "Prompt",
    helpText: "",
    paramValue: String(nodeParams.prompt ?? ""),
    inputError: undefined,
    updateParamValue: () => {},
    schema: {type: "string", "ui:optionsSource": "llm_prompt_variables"},
    nodeParams,
    nodeSchema,
    required: false,
    getNodeFieldError: () => undefined,
    readOnly: false,
  };
}

describe("TextEditorWidget tool completions", () => {
  const tools = ["one-off-reminder"];

  it("offers the node's enabled tools", () => {
    render(<TextEditorWidget {...props({name: "n", prompt: "Hi", tools})} />);

    expect(receivedTools.at(-1)).toEqual([{name: "one-off-reminder", label: "One-off Reminder"}]);
  });

  it("keeps the same tool list while only the prompt changes", () => {
    const {rerender} = render(<TextEditorWidget {...props({name: "n", prompt: "Hi", tools})} />);
    rerender(<TextEditorWidget {...props({name: "n", prompt: "Hi there", tools})} />);

    expect(receivedTools.at(-1)).toBe(receivedTools[0]);
  });

  it("rebuilds the tool list when the node's tools change", () => {
    const {rerender} = render(<TextEditorWidget {...props({name: "n", prompt: "Hi", tools})} />);
    rerender(<TextEditorWidget {...props({name: "n", prompt: "Hi", tools: []})} />);

    expect(receivedTools.at(-1)).toEqual([]);
  });

  it("offers no tools on a node type without tools", () => {
    render(<TextEditorWidget {...props({name: "n", prompt: "Hi", tools}, routerSchema)} />);

    expect(receivedTools.at(-1)).toEqual([]);
  });
});

describe("TextEditorWidget prompt help", () => {
  async function requestImprovement(nodeParams: NodeParams, nodeSchema: JsonSchema) {
    const improvePrompt = vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: "x", notes: []}});
    render(<TextEditorWidget {...props(nodeParams, nodeSchema)} />);
    fireEvent.click(screen.getByText("Help"));
    fireEvent.click(screen.getByText("Improve"));
    await waitFor(() => expect(improvePrompt).toHaveBeenCalled());
    return improvePrompt.mock.calls[0][0];
  }

  it("sends a router's keywords as its routes, with the default", async () => {
    const request = await requestImprovement(
      {name: "n", prompt: "Route it", keywords: ["BILLING", "", "SUPPORT"], default_keyword_index: 2},
      routerSchema,
    );

    expect(request).toMatchObject({node_type: "router", routes: ["BILLING", "SUPPORT"], default_route: "SUPPORT"});
  });

  it("sends no routes for an LLM node", async () => {
    const request = await requestImprovement({name: "n", prompt: "Hi", tools: [], keywords: [""]}, llmSchema);

    expect(request).toMatchObject({node_type: "llm", routes: [], default_route: ""});
  });
});
