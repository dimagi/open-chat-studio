import React from "react"
import ShortUniqueId from "short-unique-id";
import {NodeParameterValues, Option} from "./types/nodeParameterValues";
import {JsonSchema, PropertySchema} from "./types/nodeParams";

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

export function nodeBorderClass(nodeErrors : boolean, selected : boolean ): string {
  const defaultBorder = nodeErrors ? "border-error " : ""
  const selectedBorder = nodeErrors ? "border-secondary" : "border-primary"
  const border = selected ? selectedBorder : defaultBorder
  return classNames(border, "border py-2 shadow-md rounded-xl border-2")
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


export function concatenate(value: string | string[] | null | undefined): string {
  if (!value) return "";
  return Array.isArray(value) ? value.join("") : value;
}

/**
 * Retrieves select options based on the provided schema.
 * If the schema has a `ui:optionsSource`, it fetches the options from the cached parameter values.
 * Otherwise, it constructs options from the schema's enum values and their labels.
 *
 * @param {PropertySchema} schema - The schema defining the options.
 * @returns {Option[]} - An array of options for the select input.
 */
export function getSelectOptions(schema: PropertySchema): Option[] {
  const {parameterValues} = getCachedData();
  if (schema["ui:optionsSource"]) {
    return parameterValues[schema["ui:optionsSource"]];
  }

  const enums = schema.enum || [];
  const enumLabels = schema["ui:enumLabels"];
  return enums.map((value: string, index: number) => {
    const label = enumLabels ? enumLabels[index] : value;
    return {value: value, label: label};
  });
}
