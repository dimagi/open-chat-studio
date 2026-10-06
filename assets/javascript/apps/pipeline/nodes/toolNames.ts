import {NodeParams} from "../types/nodeParams";
import {NodeParameterValues, Option} from "../types/nodeParameterValues";

export type ToolCompletion = {
  name: string;
  label: string;
  note?: string;
};

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function findOption(options: Option[] | undefined, value: string): Option | undefined {
  return (options ?? []).find((option) => String(option.value) === value);
}

/** The part of a "<id>:<name>" param value after the id. */
function afterId(value: string): string {
  return value.slice(value.indexOf(":") + 1);
}

/** The tools this node gives the LLM, under the names the LLM sees them by. */
export function getEnabledToolNames(params: NodeParams, parameterValues: NodeParameterValues): ToolCompletion[] {
  const completions = new Map<string, ToolCompletion>();
  const add = (name: string, label: string) => {
    if (!completions.has(name)) {
      completions.set(name, {name, label});
    }
  };

  for (const value of asList(params.tools)) {
    add(value, findOption(parameterValues.tools, value)?.label ?? value);
  }

  for (const value of asList(params.custom_actions)) {
    const option = findOption(parameterValues.custom_actions, value);
    const name = option?.tool_name ?? afterId(value);
    const existing = completions.get(name);
    if (existing) {
      existing.note = "Name may get a suffix at runtime";
    } else {
      add(name, option?.label ?? name);
    }
  }

  for (const value of asList(params.mcp_tools)) {
    add(afterId(value), findOption(parameterValues.mcp_tools, value)?.label ?? afterId(value));
  }

  if (params.collection_id) {
    add("attach-media", "Attach Media");
  }

  // Remote indexes are searched by the provider's own tool, which has no name to refer to.
  const localIndexes = asList(params.collection_index_ids).filter(
    (id) => findOption(parameterValues.collection_index, id)?.type === "local"
  );
  if (localIndexes.length === 1) {
    add("file-search", "File Search");
  } else if (localIndexes.length > 1) {
    add("file-search-by-index", "File Search");
  }

  return Array.from(completions.values());
}
