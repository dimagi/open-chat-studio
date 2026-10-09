import {Node} from "reactflow";
import {JsonSchema, NodeData, PropertySchema} from "./types/nodeParams";
import {evaluateVisibleWhen} from "./nodes/GetInputWidget";

export type SearchMatch = {
  field: string;
  fieldLabel: string;
  snippet: string;
  /** Offset of the match within `snippet`. */
  matchStart: number;
  matchLength: number;
};

export type NodeSearchResult = {
  nodeId: string;
  nodeLabel: string;
  nodeName: string;
  matches: SearchMatch[];
};

// Widgets whose value is text the user wrote. The rest hold ids or fixed choices.
const TEXT_WIDGETS = new Set([
  "string",
  "text_editor_widget",
  "expandable_text",
  "code",
  "jinja_template",
  "keywords",
  "key_value_pairs",
]);

const SNIPPET_CONTEXT = 40;
const MAX_MATCHES_PER_FIELD = 20;

function isSearchable(property: PropertySchema, flagsEnabled: string[]): boolean {
  const requiredFlag = property["ui:flagRequired"];
  if (requiredFlag && !flagsEnabled.includes(requiredFlag)) {
    return false;
  }
  if (property["ui:widget"]) {
    return TEXT_WIDGETS.has(property["ui:widget"]);
  }
  return property.type === "string" && !property.enum && !property["ui:optionsSource"];
}

function collectStrings(value: unknown): string[] {
  if (typeof value === "string") {
    return value ? [value] : [];
  }
  if (Array.isArray(value)) {
    return value.flatMap(collectStrings);
  }
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) => [...collectStrings(key), ...collectStrings(item)]);
  }
  return [];
}

function findMatches(text: string, query: string, field: string, fieldLabel: string): SearchMatch[] {
  const matches: SearchMatch[] = [];
  const haystack = text.toLowerCase();
  let index = haystack.indexOf(query);
  while (index !== -1 && matches.length < MAX_MATCHES_PER_FIELD) {
    const start = Math.max(0, index - SNIPPET_CONTEXT);
    const end = Math.min(text.length, index + query.length + SNIPPET_CONTEXT);
    const prefix = start > 0 ? "…" : "";
    const suffix = end < text.length ? "…" : "";
    matches.push({
      field,
      fieldLabel,
      snippet: prefix + text.slice(start, end).replace(/\s/g, " ") + suffix,
      matchStart: prefix.length + index - start,
      matchLength: query.length,
    });
    index = haystack.indexOf(query, index + query.length);
  }
  return matches;
}

function orderedFields(schema: JsonSchema): string[] {
  const order = schema["ui:order"] ?? [];
  const rank = (name: string) => {
    if (name === "name") return -1;
    const index = order.indexOf(name);
    return index === -1 ? order.length : index;
  };
  return Object.keys(schema.properties).sort((a, b) => rank(a) - rank(b));
}

/** Find `query` in the text the user has written on each node. */
export function searchNodes(
  nodes: Node<NodeData>[],
  schemas: Map<string, JsonSchema>,
  query: string,
  flagsEnabled: string[],
): NodeSearchResult[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return [];
  }

  const results: NodeSearchResult[] = [];
  const inCanvasOrder = [...nodes].sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
  for (const node of inCanvasOrder) {
    const params = node.data.params ?? {};
    const schema = schemas.get(node.data.type);
    const matches: SearchMatch[] = [];

    for (const field of schema ? orderedFields(schema) : []) {
      const property = schema!.properties[field];
      // A generated name repeats the node id, which is searched below.
      if (field === "name" && params.name === node.id) continue;
      if (!isSearchable(property, flagsEnabled)) continue;
      if (!evaluateVisibleWhen(property["ui:visibleWhen"], params)) continue;
      const fieldLabel = property.title || field.replace(/_/g, " ");
      for (const text of collectStrings(params[field])) {
        matches.push(...findMatches(text, needle, field, fieldLabel));
      }
    }
    matches.push(...findMatches(node.id, needle, "id", "Node ID"));

    if (matches.length > 0) {
      results.push({
        nodeId: node.id,
        nodeLabel: schema?.["ui:label"] ?? node.data.type,
        nodeName: params.name ?? node.id,
        matches,
      });
    }
  }
  return results;
}
