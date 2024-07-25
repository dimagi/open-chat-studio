type LlmProviderIdOptions = {
  id: string;
  name: string;
};

type LlmModel = Record<string, [string]>;

export type NodeParameterValues = {
  LlmProviderId: LlmProviderIdOptions[];
  LlmModel: LlmModel;
};
