import {
  HistoryTypeWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  ExpandableTextWidget,
  InputField, LlmWidget, KeywordsWidget,
} from "../widgets";
import React from "react";
import {getCachedData, join} from "../utils";
import {InputParam} from "../types/nodeInputTypes";
import {NodeParams} from "../types/nodeParams";

type InputWidgetParams = {
  id: string;
  inputParam: InputParam;
  params: NodeParams;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
  nodeType: string;
}

const nodeTypeToInputParamsMap: Record<string, string[]> = {
  "RouterNode": ["llm_model", "history_type", "prompt"],
  "ExtractParticipantData": ["llm_model", "history_type", "data_schema"],
  "ExtractStructuredData": ["llm_model", "history_type", "data_schema"],
  "LLMResponseWithPrompt": ["llm_model", "history_type", "prompt"],
  "LLMResponse": ["llm_model", "history_type"],
};

export const showAdvancedButton = (nodeType: string) => {
  return nodeTypeToInputParamsMap[nodeType] !== undefined;
}

export const getNodeInputWidget = (params: InputWidgetParams) => {
  const allowedInNode = nodeTypeToInputParamsMap[params.nodeType];
  if (allowedInNode && !allowedInNode.includes(params.inputParam.name)) {
    return <></>;
  }
  return getInputWidget(params);
}

/**
 * Generates the appropriate input widget based on the input parameter type.
 * @param id - The node ID
 * @param inputParam - The input parameter to generate the widget for.
 * @param params - The parameters for the node.
 * @param setParams - The function to update the node parameters.
 * @param updateParamValue - The function to update the value of the input parameter.
 * @returns The input widget for the specified parameter type.
 */
export const getInputWidget = ({id, inputParam, params, updateParamValue}: InputWidgetParams) => {
  const parameterValues = getCachedData().parameterValues;
  switch (inputParam.type) {
    case "LlmTemperature":
      return (
        <InputField label="Temperature">
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name]}
            type="number"
            step=".1"
          ></input>
        </InputField>
      );
    case "SourceMaterialId":
      return (
        <InputField label="Source Material">
          <SourceMaterialIdWidget
            parameterValues={parameterValues}
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          />
        </InputField>
      );
    case "LlmProviderId":
    //   this is handled in the LlmModel widget
      return <></>;
    case "LlmModel":
      return (
        <InputField label="LLM">
          <LlmWidget
            id={id}
            parameterValues={parameterValues}
            inputParam={inputParam}
            providerId={join(params.llm_provider_id)}
            model={join(params.llm_model)}
            ></LlmWidget>
        </InputField>
      );
    case "NumOutputs":
      return <></>;
    case "Keywords": {
      return <KeywordsWidget nodeId={id} params={params}/>
    }
    case "HistoryType": {
      return (
          <HistoryTypeWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            historyType={join(params[inputParam.name])}
            historyName={join(params["history_name"])}
          ></HistoryTypeWidget>
      );
    }
    case "HistoryName": {
      return <></>;
    }
    case "MaxTokenLimit": {
      return (
        <InputField label="Maximum Token Limit">
          <MaxTokenLimitWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></MaxTokenLimitWidget>
        </InputField>
      );
    }
    case "ExpandableText": {
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <ExpandableTextWidget
          humanName={humanName}
          name={inputParam.name}
          onChange={updateParamValue}
          value={params[inputParam.name] || ""}>
        </ExpandableTextWidget>
      );
    }
    default: {
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <InputField label={humanName}>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name]}
          ></input>
        </InputField>
      );
    }
  }
};
