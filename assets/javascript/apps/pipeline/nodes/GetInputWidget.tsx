import {
  HistoryTypeWidget,
  SourceMaterialIdWidget,
  ExpandableTextWidget,
  InputField, LlmWidget, KeywordsWidget, AssistantIdWidget
} from "../widgets";
import React from "react";
import {getCachedData, concatenate} from "../utils";
import {InputParam} from "../types/nodeInputTypes";
import {NodeParams} from "../types/nodeParams";
import usePipelineManagerStore from "../stores/pipelineManagerStore";


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

export const getNodeInputWidget = (param: InputWidgetParams) => {
  if (!param.nodeType) {
    return <></>;
  }

  const allowedInNode = nodeTypeToInputParamsMap[param.nodeType];
  if (allowedInNode && !allowedInNode.includes(param.inputParam.name)) {
    return <></>;
  }
  return getInputWidget(param);
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
  if (inputParam.name == "llm_model" || inputParam.name == "max_token_limit"){
    /*
       This is here as we migrated llm_model to llm_provider_model_id, in October 2024.
       During the migration, we kept the data in llm_model as a safeguard. This check can safely be deleted once a second migration to delete all instances of llm_model has been run.
       TODO: Remove this check once there are no instances of llm_model or max_token_limit in the node definitions.
     */
    return
  }

  const getFieldError = usePipelineManagerStore((state) => state.getFieldError);
  const inputError = getFieldError(id, inputParam.name);
  const paramValue = params[inputParam.name] || "";

  switch (inputParam.type) {
    case "LlmTemperature":
      return (
        <InputField label="Temperature" help_text={inputParam.help_text} inputError={inputError}>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={paramValue}
            type="number"
            step=".1"
          ></input>
        </InputField>
      );
    case "SourceMaterialId":
      return (
        <InputField label="Source Material" help_text={inputParam.help_text} inputError={inputError}>
          <SourceMaterialIdWidget
            parameterValues={parameterValues}
            onChange={updateParamValue}
            inputParam={inputParam}
            value={paramValue}
          />
        </InputField>
      );
      case "AssistantId":
        return (
          <InputField label="Assistant" help_text={inputParam.help_text} inputError={inputError}>
            <AssistantIdWidget
              parameterValues={parameterValues}
              onChange={updateParamValue}
              inputParam={inputParam}
              value={paramValue}
            />
          </InputField>
        );
      case "LlmProviderModelId":
          //   this is handled in the LlmModel widget
      return <></>;
      case "LlmProviderId":
      return (
        <InputField label="LLM" help_text={inputParam.help_text} inputError={inputError}>
          <LlmWidget
            id={id}
            parameterValues={parameterValues}
            inputParam={inputParam}
            providerId={concatenate(params.llm_provider_id)}
            providerModelId={concatenate(params.llm_provider_model_id)}
            ></LlmWidget>
        </InputField>
      );
    case "NumOutputs":
      return <></>;
    case "Keywords": {
      return <KeywordsWidget nodeId={id} params={params} inputError={inputError}/>
    }
    case "HistoryType": {
      return (
          <HistoryTypeWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            historyType={concatenate(paramValue)}
            historyName={concatenate(params["history_name"])}
            help_text={inputParam.help_text}
          ></HistoryTypeWidget>
      );
    }
    case "HistoryName": {
      return <></>;
    }
    case "ExpandableText": {
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <ExpandableTextWidget
          humanName={humanName}
          name={inputParam.name}
          onChange={updateParamValue}
          value={paramValue}
          help_text={inputParam.help_text}
          inputError={inputError}>
        </ExpandableTextWidget>
      );
    }
    case "ToggleField": {
      const onChangeCallback = (event: React.ChangeEvent<HTMLInputElement>) => {
        event.target.value = event.target.checked ? "true" : "false";
        updateParamValue(event);
      };
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <InputField label={humanName} help_text={inputParam.help_text} inputError={inputError}>
          <input
            className="toggle"
            name={inputParam.name}
            onChange={onChangeCallback}
            checked={paramValue === "true"}
            type="checkbox"
          ></input>
        </InputField>
      );
    }
    default: {
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <InputField label={humanName} help_text={inputParam.help_text} inputError={inputError}>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={paramValue}
            type="text"
          ></input>
        </InputField>
      );
    }
  }
};
