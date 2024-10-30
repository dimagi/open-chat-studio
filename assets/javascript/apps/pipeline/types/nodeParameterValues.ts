type LlmProviderIdOptions = {
  id: string;
  name: string;
  type: string;
};

type LlmProviderModelIdOptions = {
  id: string;
  name: string;
  type: string;
};

type LlmModel = Record<string, [string]>;
type SourceMaterialIdOptions = {
  id: string;
  topic: string;
};

export type NodeParameterValues = {
  LlmProviderId: LlmProviderIdOptions[];
  LlmProviderModelId: LlmProviderModelIdOptions[];
  LlmModel: LlmModel;
  SourceMaterialId: SourceMaterialIdOptions[];
};
