import React from "react"
import ShortUniqueId from "short-unique-id";
import {NodeParameterValues, Option} from "./types/nodeParameterValues";
import {JsonSchema, NodeParams, PropertySchema} from "./types/nodeParams";

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

const localCache = {
  loaded: false,
  nodeSchemas: null as unknown as Map<string, JsonSchema>,
  parameterValues: null as unknown as NodeParameterValues,
  defaultValues: null as unknown as Record<string, any>,
  flagsEnabled: null as unknown as Array<string>,
  modelParams: null as unknown as Record<string, string>,
  modelParamSchemas: null as unknown as Record<string, any>,
};

export const getCachedData: () => typeof localCache = () => {
  if (!localCache.loaded) {
    localCache.parameterValues = JSON.parse(document.getElementById("parameter-values")?.textContent || "{}");
    localCache.defaultValues = JSON.parse(document.getElementById("default-values")?.textContent || "{}");
    const schemas = JSON.parse(document.getElementById("node-schemas")?.textContent || "[]");
    localCache.nodeSchemas = new Map(schemas.map((schema: any) => [schema.title, schema]));
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
 * The param values a fresh node of this schema starts with: each property's own default,
 * falling back to the team's cached default-values map, or null when neither has one.
 */
export function getDefaultParamValues(schema: JsonSchema): NodeParams {
  const {defaultValues} = getCachedData();
  const defaults: NodeParams = {name: ""};
  for (const name in schema.properties) {
    // Every real node schema declares "name" as one of its own properties (it's a field on the
    // shared base node), but it's never given a schema or cached default here -- it's set by
    // the caller (a generated id on add, the preserved name on a type change).
    if (name === "name") continue;
    const property = schema.properties[name];
    defaults[name] = [property.default, defaultValues[name]].find((value) => value !== undefined && value !== null) ?? null;
  }
  return defaults;
}

/**
 * The param values a node keeps when its type changes to `newSchema` (#1452).
 *
 * Every type-specific param resets to the new type's own defaults, never carried across by
 * matching field names -- a name shared between two schemas is not proof it means the same
 * thing in both (see `NodeUpdateSerializer` on the v2 API, which refuses a type change in place
 * for exactly this reason). `name` and `color` are the only survivors: `name` is UI identity
 * every schema declares, and `color` is UI-only state that is not a schema field on any node
 * type at all, so it is carried separately rather than lost to the reset.
 */
export function buildTypeChangeParams(newSchema: JsonSchema, currentParams: NodeParams): NodeParams {
  const params = getDefaultParamValues(newSchema);
  params.name = currentParams.name;
  if (currentParams.color !== undefined) {
    params.color = currentParams.color;
  }
  return params;
}

export function concatenate(value: string | string[] | null | undefined): string {
  if (!value) return "";
  return Array.isArray(value) ? value.join("") : value;
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
