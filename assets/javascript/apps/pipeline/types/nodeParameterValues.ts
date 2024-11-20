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

type Assistant = {
  id: string;
  name: string;
};

export type NodeParameterValues = {
  LlmProviderId: LlmProvider[];
  LlmProviderModelId: LlmProviderModel[];
  LlmModel: LlmModel;
  SourceMaterialId: SourceMaterial[];
  AssistantId: Assistant[];
  InternalToolsField: string[][];
};
