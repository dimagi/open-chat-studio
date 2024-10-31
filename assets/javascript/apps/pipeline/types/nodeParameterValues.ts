type LlmProvider = {
  id: string;
  name: string;
  type: string;
};

export type LlmProviderModel = {
  id: string;
  name: string;
  type: string;
};

type LlmModel = Record<string, [string]>;
type SourceMaterial = {
  id: string;
  topic: string;
};

export type NodeParameterValues = {
  LlmProviderId: LlmProvider[];
  LlmProviderModelId: LlmProviderModel[];
  LlmModel: LlmModel;
  SourceMaterialId: SourceMaterial[];
};
