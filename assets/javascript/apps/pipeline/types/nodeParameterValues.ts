export type Option = {
  value: string;
  label: string;
}

export type TypedOption = {
  value: string;
  label: string;
  type: string;
}


export type NodeParameterValues = {
  LlmProviderId: TypedOption[];
  LlmProviderModelId: TypedOption[];
  [k: string]: Option[];
};
