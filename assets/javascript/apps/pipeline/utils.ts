import ShortUniqueId from "short-unique-id";
import {NodeParameterValues} from "./types/nodeParameterValues";

const uid = new ShortUniqueId({ length: 5 });

export function getNodeId(nodeType: string) {
  return nodeType + "-" + uid.rnd();
}

export function classNames(...classes: Array<string | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

const localCache = {
  loaded: false,
  nodeSchemas: null as unknown as Map<string, any>,
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
