from typing_extensions import TypeAliasType

HistoryName = TypeAliasType("HistoryName", str)
HistoryType = TypeAliasType("HistoryType", str)
Keywords = TypeAliasType("Keywords", list)
LlmModel = TypeAliasType("LlmModel", str)
LlmProviderId = TypeAliasType("LlmProviderId", int)
LlmTemperature = TypeAliasType("LlmTemperature", float)
MaxTokenLimit = TypeAliasType("MaxTokenLimit", int)
NumOutputs = TypeAliasType("NumOutputs", int)
SourceMaterialId = TypeAliasType("SourceMaterialId", int)

ExpandableText = TypeAliasType("ExpandableText", str)
