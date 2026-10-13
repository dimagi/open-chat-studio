import {describe, expect, it} from "vitest";
import {NodeParameterValues} from "../types/nodeParameterValues";
import {getEnabledToolNames} from "./toolNames";

const parameterValues = {
  tools: [
    {value: "one-off-reminder", label: "One-off Reminder"},
    {value: "update-user-data", label: "Update Participant Data"},
  ],
  custom_actions: [
    {value: "7:weather_get", label: "Weather: Get the weather", tool_name: "weather_get"},
    {value: "7:get weather", label: "Weather: Today", tool_name: "get_weather"},
    {value: "8:get/weather", label: "Forecast: Today", tool_name: "get_weather"},
  ],
  mcp_tools: [{value: "3:search:docs", label: "Docs server: search:docs"}],
  collection_index: [
    {value: 1, label: "Policies (Local)", type: "local"},
    {value: 2, label: "Manuals (Local)", type: "local"},
    {value: 3, label: "Hosted (Remote)", type: "remote"},
  ],
} as unknown as NodeParameterValues;

function names(params: Record<string, unknown>) {
  return getEnabledToolNames({name: "node", ...params}, parameterValues);
}

describe("getEnabledToolNames", () => {
  it("returns nothing for a node with no tools", () => {
    expect(names({tools: [], custom_actions: null})).toEqual([]);
  });

  it.each([
    ["an agent tool", {tools: ["one-off-reminder"]}, [{name: "one-off-reminder", label: "One-off Reminder"}]],
    ["an agent tool with no option", {tools: ["calculator"]}, [{name: "calculator", label: "calculator"}]],
    ["a custom action", {custom_actions: ["7:weather_get"]}, [{name: "weather_get", label: "Weather: Get the weather"}]],
    ["a custom action with no option", {custom_actions: ["9:lookup"]}, [{name: "lookup", label: "lookup"}]],
    ["an MCP tool", {mcp_tools: ["3:search:docs"]}, [{name: "search:docs", label: "Docs server: search:docs"}]],
    ["a value with no id", {custom_actions: ["lookup"]}, [{name: "lookup", label: "lookup"}]],
    ["a media collection", {collection_id: 5}, [{name: "attach-media", label: "Attach Media"}]],
    ["one local index", {collection_index_ids: [1]}, [{name: "file-search", label: "File Search"}]],
    ["several local indexes", {collection_index_ids: [1, "2"]}, [{name: "file-search-by-index", label: "File Search"}]],
    ["a remote index", {collection_index_ids: [3]}, []],
    ["built-in provider tools", {built_in_tools: ["web-search"]}, []],
  ])("derives the name for %s", (_name, params, expected) => {
    expect(names(params)).toEqual(expected);
  });

  it("offers a name shared by two custom actions once, with a note", () => {
    expect(names({custom_actions: ["7:get weather", "8:get/weather"]})).toEqual([
      {name: "get_weather", label: "Weather: Today", note: "Name may get a suffix at runtime"},
    ]);
  });

  it("lists each name once", () => {
    expect(names({tools: ["one-off-reminder", "one-off-reminder"]})).toHaveLength(1);
  });
});
