export type Option = {
  value: string;
  label: string;
  edit_url?: string | undefined;
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
