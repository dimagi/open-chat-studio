import ShortUniqueId from "short-unique-id";
import {NodeParameterValues, Option} from "./types/nodeParameterValues";
import {JsonSchema, PropertySchema} from "./types/nodeParams";

const uid = new ShortUniqueId({ length: 5 });

export function getNodeId(nodeType: string) {
  return nodeType + "-" + uid.rnd();
}

/**
 * Combines multiple class names into a single string.
 * 
 * @param classes - A variable number of class names, which can include null or undefined values
 * @returns A space-separated string of non-null class names
 * 
 * @remarks
 * This utility function filters out falsy values (null, undefined) and joins the remaining class names.
 * 
 * @example
 * classNames('btn', 'primary', null, 'active') // Returns 'btn primary active'
 * classNames(undefined, 'disabled') // Returns 'disabled'
 * classNames() // Returns an empty string
 */
export function classNames(...classes: Array<string | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

/**
 * Determines the CSS border class for a node based on its error and selection state.
 *
 * @param nodeErrors - Indicates whether the node has errors
 * @param selected - Indicates whether the node is currently selected
 * @returns A string of CSS classes defining the node's border styling
 *
 * @remarks
 * The border class is determined by two factors:
 * - If the node has errors, it uses an error border
 * - If the node is selected, it uses a different border color
 */
export function nodeBorderClass(nodeErrors : boolean, selected : boolean ): string {
  const defaultBorder = nodeErrors ? "border-error " : ""
  const selectedBorder = nodeErrors ? "border-secondary" : "border-primary"
  const border = selected ? selectedBorder : defaultBorder
  return classNames(border, "border py-2 shadow-md rounded-xl border-2 bg-base-100")
}

const localCache = {
  loaded: false,
  nodeSchemas: null as unknown as Map<string, JsonSchema>,
  parameterValues: null as unknown as NodeParameterValues,
  defaultValues: null as unknown as Record<string, any>,
};

export const getCachedData: () => typeof localCache = () => {
  if (!localCache.loaded) {
    localCache.parameterValues = JSON.parse(document.getElementById("parameter-values")?.textContent || "{}");
    localCache.defaultValues = JSON.parse(document.getElementById("default-values")?.textContent || "{}");
    const schemas = JSON.parse(document.getElementById("node-schemas")?.textContent || "[]");
    localCache.nodeSchemas = new Map(schemas.map((schema: any) => [schema.title, schema]));
  }
  return localCache;
};

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
