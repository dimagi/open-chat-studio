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
  parameterValues: null as unknown as NodeParameterValues,
  defaultValues: null as unknown as Record<string, any>,
};

export const getCachedData: () => typeof localCache = () => {
  if (!localCache.parameterValues) {
    localCache.parameterValues = JSON.parse(document.getElementById("parameter-values")?.textContent || "{}");
  }
  if (!localCache.defaultValues) {
    localCache.defaultValues = JSON.parse(document.getElementById("default-values")?.textContent || "{}");
  }
  return localCache;
};
