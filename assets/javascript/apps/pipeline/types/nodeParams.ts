export type NodeParams = {
  name: string;
  [key: string]: any;
}

export type PropertySchema = {
  type: string;
  title?: string | undefined;
  description?: string | undefined;
  default?: any | undefined;
  enum?: string[] | undefined;
  "ui:optionsSource"?: string | undefined;
  "ui:widget"?: string | undefined;
  "ui:enumLabels"?: string[] | undefined;
  "ui:enumConditionalValues"?: Record<string, string[]>;
  "ui:conditionalField"?: string | undefined;
  "ui:flagRequired"?: string | undefined;
  "additionalProperties"?: {
    anyOf?: JsonSchema[] | undefined;
  }
  [k: string]: any;
}

// cut down version of JsonSchema
// See https://github.com/DefinitelyTyped/DefinitelyTyped/blob/master/types/json-schema/index.d.ts
export type JsonSchema = {
  title: string;
  description?: string | undefined;
  required?: string[] | undefined;
  "ui:flow_node_type": string;
  "ui:label": string;
  "ui:can_add": boolean;
  "ui:can_delete": boolean;
  "ui:deprecated": boolean;
  "ui:deprecation_message"?: string;
  "ui:documentation_link"?: string;
  "ui:order"?: string[];
  properties: Record<string, PropertySchema>;
  [k: string]: any;
}

export type NodeData = {
  type: string;
  label: string;
  flowType?: string;
  params: NodeParams;
};
