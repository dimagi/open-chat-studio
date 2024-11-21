export type NodeParams = Record<string, string | string[]>;

export type PropertySchema = {
  type: string;
  title?: string | undefined;
  description?: string | undefined;
  default?: any | undefined;
  enum?: string[] | undefined;
  "ui:optionsSource"?: string | undefined;
  "ui:widget"?: string | undefined;
  "ui:enumLabels"?: string | undefined;
  [k: string]: any;
}

// cut down version of JsonSchema
// See https://github.com/DefinitelyTyped/DefinitelyTyped/blob/master/types/json-schema/index.d.ts
export type JsonSchema = {
  title: string;
  description?: string | undefined;
  required?: string[] | undefined;
  "ui:label": string;
  properties: Record<string, PropertySchema>;
  [k: string]: any;
}

export type NodeData = {
  type: string;
  label: string;
  params: NodeParams;
};
