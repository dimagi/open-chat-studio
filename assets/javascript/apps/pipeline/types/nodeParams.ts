export type NodeParams = Record<string, string | string[]>;

export type InputSchema = {
  type: string;
  title?: string | undefined;
  description?: string | undefined;
  default?: any | undefined;
  "ui:optionsSource"?: string | undefined;
  "ui:widget"?: string | undefined;
  [k: string]: any;
}

export type NodeData = {
  label: string;
  value: number;
  type: string;
  paramNames: string[];
  inputParams: Record<string, InputSchema>;
  params: NodeParams;
};
