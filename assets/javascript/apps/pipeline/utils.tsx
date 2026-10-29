import React from "react"
import {addEdge, Connection} from "reactflow";
import ShortUniqueId from "short-unique-id";
import {NodeParameterValues, Option} from "./types/nodeParameterValues";
import {JsonSchema, NodeData, NodeParams, PropertySchema} from "./types/nodeParams";

declare global {
  interface Window {
    DOCUMENTATION_BASE_URL: string;
  }
}

const uid = new ShortUniqueId({ length: 5 });

export function getNodeId(nodeType: string) {
  return nodeType + "-" + uid.rnd();
}

export function classNames(...classes: Array<string | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

export function nodeBorderClass(nodeErrors : boolean, selected : boolean, nodeWarnings = false): string {
  if (nodeErrors) {
    return classNames(selected ? "border-secondary" : "border-error", "border py-2 shadow-md rounded-xl border-2")
  }
  if (nodeWarnings) {
    return classNames(selected ? "border-warning" : "border-warning/60", "border py-2 shadow-md rounded-xl border-2")
  }
  return classNames(selected ? "border-primary" : "", "border py-2 shadow-md rounded-xl border-2")
}

/** The standard router prompt's versions, for the builder's preview. */
export interface RouterPromptTemplates {
  latest: string;
  templates: Record<string, string>;
}

const localCache = {
  loaded: false,
  nodeSchemas: null as unknown as Map<string, JsonSchema>,
  parameterValues: null as unknown as NodeParameterValues,
  defaultValues: null as unknown as Record<string, unknown>,
  routerPromptTemplates: null as unknown as RouterPromptTemplates,
  flagsEnabled: null as unknown as Array<string>,
  modelParams: null as unknown as Record<string, string>,
  modelParamSchemas: null as unknown as Record<string, JsonSchema>,
};

export const getCachedData: () => typeof localCache = () => {
  if (!localCache.loaded) {
    localCache.parameterValues = JSON.parse(document.getElementById("parameter-values")?.textContent || "{}");
    localCache.defaultValues = JSON.parse(document.getElementById("default-values")?.textContent || "{}");
    localCache.routerPromptTemplates = JSON.parse(
      document.getElementById("router-prompt-templates")?.textContent || '{"latest": "", "templates": {}}'
    );
    const schemas = JSON.parse(document.getElementById("node-schemas")?.textContent || "[]");
    localCache.nodeSchemas = new Map(schemas.map((schema: JsonSchema) => [schema.title, schema]));
    localCache.flagsEnabled = JSON.parse(document.getElementById("flags-enabled")?.textContent || "[]");
    localCache.modelParams = JSON.parse(document.getElementById("llm-model-params")?.textContent || "{}");
    localCache.modelParamSchemas = JSON.parse(document.getElementById("llm-model-parameter-schemas")?.textContent || "{}");
    localCache.loaded = true;
  }
  return localCache;
};


export function formatDocsForSchema(schema: JsonSchema)  {
  const description = schema.description || "";
  const documentationLink = getDocumentationLink(schema);
  if (!description && !documentationLink) {
    return null;
  }
  return <>
    <p>{description}</p>
    {documentationLink && <p><a className="link" href={documentationLink} target="_blank">Learn more</a></p>}
  </>;
}


export function getDocumentationLink(schema: JsonSchema) {
  let documentationLink = schema["ui:documentation_link"];
  if (!documentationLink) {
    return null;
  }
  if (documentationLink && !documentationLink.startsWith("http")) {
    documentationLink = `${window.DOCUMENTATION_BASE_URL}${documentationLink}`;
  }
  return documentationLink;
}


/**
 * Coerce a node param to a string. Takes `unknown` because node params are backend-supplied
 * JSON with no compile-time shape -- see `NodeParams`.
 */
export function concatenate(value: unknown): string {
  if (!value) return "";
  return Array.isArray(value) ? value.join("") : String(value);
}

/**
 * Retrieves select options based on the provided schema.
 * If the schema has a `ui:optionsSource`, it fetches the options from the cached parameter values.
 * Otherwise, it constructs options from the schema's enum values and their labels.
 *
 * A named source with nothing behind it yields no options rather than `undefined`: the backend
 * stops serving a source as soon as the resource it lists is removed, while nodes already stored
 * with that `ui:optionsSource` keep rendering. Callers map over the result, so returning
 * `undefined` here throws and takes the whole editor down with it.
 *
 * @param {PropertySchema} schema - The schema defining the options.
 * @returns {Option[]} - An array of options for the select input.
 */
export function getSelectOptions(schema: PropertySchema): Option[] {
  const {parameterValues} = getCachedData();
  if (schema["ui:optionsSource"]) {
    return parameterValues[schema["ui:optionsSource"]] ?? [];
  }

  const enums = schema.enum || [];
  const enumLabels = schema["ui:enumLabels"];
  return enums.map((value: string, index: number) => {
    const label = enumLabels ? enumLabels[index] : value;
    return {value: value, label: label};
  });
}

/**
 * Build the default params for a node of this type: the schema's own default for each
 * property, falling back to the site-wide default for a property of that name.
 */
export function getDefaultParamValues(schema: JsonSchema): NodeParams {
  const {defaultValues} = getCachedData();
  const defaults: NodeParams = {name: ""};
  for (const name in schema.properties) {
    const property = schema.properties[name];
    defaults[name] = [property.default, defaultValues[name]].find(
      (value) => value !== undefined && value !== null
    ) ?? null;
  }
  return defaults;
}

/** A fresh node of this type, before it is given an id and a place on the canvas. */
export function nodeDataFromSchema(schema: JsonSchema): NodeData {
  return {
    type: schema.title,
    label: schema["ui:label"],
    params: getDefaultParamValues(schema),
  };
}

/** The node types a user can add to a pipeline, in the order they are offered. */
export function addableNodeSchemas(): JsonSchema[] {
  return Array.from(getCachedData().nodeSchemas.values())
    .filter((schema) => schema["ui:can_add"])
    .sort((a, b) => a["ui:label"].localeCompare(b["ui:label"]));
}

// Routers whose outputs come from a user-supplied keyword list. The backend does not publish
// this in the node schema, so the editor has to name the types.
const KEYWORD_ROUTER_NODE_TYPES = ["RouterNode", "StaticRouterNode"];

// The node types that fan out to several outputs: the keyword routers, plus BooleanNode, whose
// two outputs are fixed.
const MULTI_OUTPUT_NODE_TYPES = ["BooleanNode", ...KEYWORD_ROUTER_NODE_TYPES];

export function isKeywordRouter(nodeType: string): boolean {
  return KEYWORD_ROUTER_NODE_TYPES.includes(nodeType);
}

export function hasMultipleOutputs(nodeType: string): boolean {
  return MULTI_OUTPUT_NODE_TYPES.includes(nodeType);
}

/** The handle id of a node type's nth output. */
export function outputHandle(nodeType: string, index: number): string {
  return hasMultipleOutputs(nodeType) ? `output_${index}` : "output";
}

/**
 * The edge id React Flow itself would draw for these ends, so edges the editor builds are
 * indistinguishable from ones the user drew by hand. React Flow does not export the format,
 * so `addEdge` is asked to draw one instead of restating it here.
 */
export function getEdgeId(
  source: string,
  sourceHandle: string | null | undefined,
  target: string,
  targetHandle: string | null | undefined,
): string {
  const connection: Connection = {
    source,
    sourceHandle: sourceHandle ?? null,
    target,
    targetHandle: targetHandle ?? null,
  };
  return addEdge(connection, [])[0].id;
}

/**
 * The standard router prompt as the router would send it, for the builder's preview. Mirrors
 * `render_router_prompt` and `RouterNode._format_routes` on the server; variables only known at run
 * time, such as `{participant_data}`, are left as written. Both sides are tested against the cases in
 * `apps/pipelines/tests/data/router_prompt_preview_cases.json`.
 */
export function renderRouterPromptPreview(
  template: string,
  {keywords, routeDescriptions, customContext, defaultRoute}: {
    keywords: string[];
    routeDescriptions: Record<string, string>;
    customContext: string;
    defaultRoute: string;
  },
): string {
  const routeNames = keywords;
  const routes = routeNames.map((keyword) => {
    const description = (routeDescriptions[keyword] || "").trim();
    return keyword ? (description ? `- ${keyword}: ${description}` : `- ${keyword}`) : null;
  }).filter((line): line is string => line !== null).join("\n");
  const routeVariables = {routes, routes_list: routeNames.join(", ")};
  const context = fillPromptVariables(customContext.trim(), routeVariables);
  const contextSection = context ? `\nAdditional context:\n${context}\n` : "";
  const escapedDefaultRoute = defaultRoute.replace(/\{/g, "{{").replace(/\}/g, "}}");
  return fillPromptVariables(template, {...routeVariables, default_route: escapedDefaultRoute, custom_context: contextSection});
}

/** Fills `{name}` variables in one pass, as Python's `str.format` does: `{{` and `}}` are literal braces. */
function fillPromptVariables(text: string, values: Record<string, string>): string {
  return text.replace(/\{\{|\}\}|\{(\w+)\}/g, (token, name?: string) => {
    if (token === "{{") return "{";
    if (token === "}}") return "}";
    return name !== undefined && Object.hasOwn(values, name) ? values[name] : token;
  });
}
