import {
  HistoryTypeWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  ExpandableTextWidget,
  InputField, LlmWidget, KeywordsWidget,
} from "../widgets";
import React, {useEffect, ChangeEvent } from "react";
import {getCachedData, concatenate} from "../utils";
import {InputParam} from "../types/nodeInputTypes";
import {NodeParams} from "../types/nodeParams";
import {validateFieldValue} from "./InputValidators";
import useNodeErrorStore from "../stores/nodeErrorStore";


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
  const setFieldError = useNodeErrorStore((state) => state.setFieldError);
  const clearFieldErrors = useNodeErrorStore((state) => state.clearFieldErrors);

  if (!param.nodeType) {
    return <></>;
  }

  // Validate [all] inputs so we know when there's errors on load
  const {id, inputParam, params} = param;
  validateFieldValue({value: params[inputParam.name], nodeId: id, fieldName: inputParam.name, validators: inputParam.validators, clearErrorFunc: clearFieldErrors, setErrorFunc: setFieldError});

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
  const fieldError = useNodeErrorStore((state) => state.fieldError);
  const setFieldError = useNodeErrorStore((state) => state.setFieldError);
  const clearFieldErrors = useNodeErrorStore((state) => state.clearFieldErrors);

  const onChangeCallbacks = (event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => {
    updateParamValue(event);
    validateFieldValue({value: event.target.value, nodeId: id, fieldName: inputParam.name, validators: inputParam.validators, clearErrorFunc: clearFieldErrors, setErrorFunc: setFieldError});
  }

  useEffect(() => {
      validateFieldValue({value: params[inputParam.name], nodeId: id, fieldName: inputParam.name, validators: inputParam.validators, clearErrorFunc: clearFieldErrors, setErrorFunc: setFieldError});
  }, []);

  const inputError = fieldError(id, inputParam.name);

  switch (inputParam.type) {
    case "LlmTemperature":
      return (
        <InputField label="Temperature" help_text={inputParam.help_text} inputError={inputError}>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={onChangeCallbacks}
            value={params[inputParam.name]}
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
            onChange={onChangeCallbacks}
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
        <InputField label="LLM" help_text={inputParam.help_text} inputError={inputError}>
          <LlmWidget
            id={id}
            parameterValues={parameterValues}
            inputParam={inputParam}
            providerId={concatenate(params.llm_provider_id)}
            model={concatenate(params.llm_model)}
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
            onChange={onChangeCallbacks}
            inputParam={inputParam}
            historyType={concatenate(params[inputParam.name])}
            historyName={concatenate(params["history_name"])}
            help_text={inputParam.help_text}
          ></HistoryTypeWidget>
      );
    }
    case "HistoryName": {
      return <></>;
    }
    case "MaxTokenLimit": {
      return (
        <InputField label="Maximum Token Limit" help_text={inputParam.help_text} inputError={inputError}>
          <MaxTokenLimitWidget
            onChange={onChangeCallbacks}
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
          onChange={onChangeCallbacks}
          value={params[inputParam.name] || ""}
          help_text={inputParam.help_text}
          inputError={inputError}>
        </ExpandableTextWidget>
      );
    }
    default: {
      const humanName = inputParam.name.replace(/_/g, " ");
      return (
        <InputField label={humanName} help_text={inputParam.help_text} inputError={inputError}>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={onChangeCallbacks}
            value={params[inputParam.name]}
            type="text"
          ></input>
        </InputField>
      );
    }
  }
};
