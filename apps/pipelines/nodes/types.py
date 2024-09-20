from typing_extensions import TypeAliasType

LlmProviderId = TypeAliasType("LlmProviderId", int)
LlmModel = TypeAliasType("LlmModel", str)
LlmTemperature = TypeAliasType("LlmTemperature", float)
PipelineJinjaTemplate = TypeAliasType("PipelineJinjaTemplate", str)
SourceMaterialId = TypeAliasType("SourceMaterialId", int)
NumOutputs = TypeAliasType("NumOutputs", int)
Keywords = TypeAliasType("Keywords", list)
HistoryType = TypeAliasType("HistoryType", str | None)
HistoryName = TypeAliasType("HistoryName", str)
MaxTokenLimit = TypeAliasType("MaxTokenLimit", int)
