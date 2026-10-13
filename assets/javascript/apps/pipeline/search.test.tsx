import {describe, expect, it} from "vitest";
import {Node} from "reactflow";
import {JsonSchema, NodeData, NodeParams} from "./types/nodeParams";
import {searchNodes} from "./search";

const schemaDefaults = {
  "ui:flow_node_type": "pipelineNode",
  "ui:can_add": true,
  "ui:can_delete": true,
  "ui:deprecated": false,
};

const llmSchema: JsonSchema = {
  ...schemaDefaults,
  title: "LLMResponseWithPrompt",
  "ui:label": "LLM",
  "ui:order": ["prompt", "tag"],
  properties: {
    name: {type: "string", title: "Node Name"},
    tag: {type: "string"},
    prompt: {type: "string", "ui:widget": "text_editor_widget", "ui:optionsSource": "llm_prompt_variables"},
    llm_provider_id: {type: "integer", "ui:widget": "llm_provider_model", "ui:optionsSource": "llm_provider_id"},
    source_material_id: {type: "string", "ui:widget": "select", "ui:optionsSource": "source_material"},
    history_type: {type: "string", enum: ["global", "node"]},
    tool_config: {type: "object", "ui:widget": "none"},
    mcp_tools: {type: "array", "ui:widget": "multiselect", "ui:flagRequired": "flag_mcp"},
    secret_note: {type: "string", "ui:flagRequired": "flag_notes"},
    max_results: {type: "string", "ui:visibleWhen": {field: "tag", operator: "is_not_empty", value: null}},
    headers: {type: "object", "ui:widget": "key_value_pairs"},
  },
};

const routerSchema: JsonSchema = {
  ...schemaDefaults,
  title: "RouterNode",
  "ui:label": "LLM Router",
  properties: {
    name: {type: "string"},
    keywords: {type: "array", "ui:widget": "keywords"},
  },
};

const schemas = new Map([llmSchema, routerSchema].map((schema) => [schema.title, schema]));

function node(
  id: string, type: string, params: Partial<NodeParams>, position = {x: 0, y: 0}
): Node<NodeData> {
  return {id, position, data: {type, label: type, params: {name: id, ...params}}};
}

function search(nodes: Node<NodeData>[], query: string, flags: string[] = []) {
  return searchNodes(nodes, schemas, query, flags);
}

describe("searchNodes", () => {
  it.each([
    ["empty", ""],
    ["whitespace only", "   "],
  ])("returns nothing for a query that is %s", (_name, query) => {
    expect(search([node("n1", "LLMResponseWithPrompt", {prompt: "hello"})], query)).toEqual([]);
  });

  it("matches case-insensitively and reports the node and field", () => {
    const results = search([node("LLM-abc12", "LLMResponseWithPrompt", {name: "Greeter", prompt: "Use Temp_State here"})], "temp_state");
    expect(results).toEqual([
      {
        nodeId: "LLM-abc12",
        nodeLabel: "LLM",
        nodeName: "Greeter",
        matches: [{field: "prompt", fieldLabel: "prompt", snippet: "Use Temp_State here", matchStart: 4, matchLength: 10}],
      },
    ]);
  });

  it("finds every occurrence in one field", () => {
    const [result] = search([node("n1", "LLMResponseWithPrompt", {prompt: "foo bar foo"})], "foo");
    expect(result.matches.map((match) => match.matchStart)).toEqual([0, 8]);
  });

  it("searches the entries of a keywords list", () => {
    const [result] = search([node("n1", "RouterNode", {keywords: ["BILLING", "", "SUPPORT"]})], "support");
    expect(result.matches).toEqual([
      {field: "keywords", fieldLabel: "keywords", snippet: "SUPPORT", matchStart: 0, matchLength: 7},
    ]);
  });

  it("searches the keys and values of a key-value field", () => {
    const [result] = search([node("n1", "LLMResponseWithPrompt", {headers: {"X-Token": "abc", other: "token-2"}})], "token");
    expect(result.matches.map((match) => match.snippet)).toEqual(["X-Token", "token-2"]);
  });

  it("matches the node id and the node name", () => {
    const nodes = [
      node("RouterNode-abc12", "RouterNode", {name: "RouterNode-abc12"}),
      node("RouterNode-xyz99", "RouterNode", {name: "route the abc12 case"}),
    ];
    const results = search(nodes, "abc12");
    expect(results.map((result) => result.matches.map((match) => match.fieldLabel))).toEqual([["Node ID"], ["name"]]);
  });

  it("uses the schema title as the field label", () => {
    const [result] = search([node("n1", "LLMResponseWithPrompt", {name: "Greeter"})], "greeter");
    expect(result.matches[0].fieldLabel).toBe("Node Name");
  });

  it.each([
    ["a select field holding an id", {source_material_id: "needle"}],
    ["an enum field", {history_type: "needle"}],
    ["a field with no widget", {tool_config: {search: "needle"}}],
    ["a field behind a disabled flag", {secret_note: "needle"}],
    ["a field hidden by ui:visibleWhen", {max_results: "needle", tag: ""}],
    ["a param missing from the schema", {legacy: "needle"}],
  ])("skips %s", (_name, params) => {
    expect(search([node("n1", "LLMResponseWithPrompt", params)], "needle")).toEqual([]);
  });

  it("searches a flag-gated field once the flag is enabled", () => {
    const results = search([node("n1", "LLMResponseWithPrompt", {secret_note: "needle"})], "needle", ["flag_notes"]);
    expect(results).toHaveLength(1);
  });

  it("orders fields with name first, then by ui:order", () => {
    const [result] = search([node("n1", "LLMResponseWithPrompt", {name: "x needle", tag: "needle", prompt: "needle"})], "needle");
    expect(result.matches.map((match) => match.field)).toEqual(["name", "prompt", "tag"]);
  });

  it("trims the snippet around the match and flattens newlines", () => {
    const prompt = "a".repeat(100) + "\nneedle\n" + "b".repeat(100);
    const [result] = search([node("n1", "LLMResponseWithPrompt", {prompt})], "needle");
    const {snippet, matchStart, matchLength} = result.matches[0];
    expect(snippet).toBe("…" + "a".repeat(39) + " needle " + "b".repeat(39) + "…");
    expect(snippet.slice(matchStart, matchStart + matchLength)).toBe("needle");
  });

  it("lists nodes in canvas order, left to right and then top to bottom", () => {
    const nodes = [
      node("right", "LLMResponseWithPrompt", {prompt: "needle"}, {x: 500, y: 0}),
      node("left-lower", "LLMResponseWithPrompt", {prompt: "needle"}, {x: 0, y: 200}),
      node("left-upper", "LLMResponseWithPrompt", {prompt: "needle"}, {x: 0, y: 0}),
    ];

    expect(search(nodes, "needle").map((result) => result.nodeId)).toEqual(["left-upper", "left-lower", "right"]);
  });

  it("searches a node with no schema by id only", () => {
    const results = search([node("StartNode-1", "StartNode", {prompt: "start"})], "start");
    expect(results).toEqual([
      {
        nodeId: "StartNode-1",
        nodeLabel: "StartNode",
        nodeName: "StartNode-1",
        matches: [{field: "id", fieldLabel: "Node ID", snippet: "StartNode-1", matchStart: 0, matchLength: 5}],
      },
    ]);
  });
});
