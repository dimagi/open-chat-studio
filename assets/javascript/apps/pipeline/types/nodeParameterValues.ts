type LlmProviderIdOptions = {
  id: string;
  name: string;
};

type LlmModel = Record<string, [string]>;
type SourceMaterialIdOptions = {
  id: string;
  topic: string;
};

export type NodeParameterValues = {
  LlmProviderId: LlmProviderIdOptions[];
  LlmModel: LlmModel;
  SourceMaterialId: SourceMaterialIdOptions[];
};
