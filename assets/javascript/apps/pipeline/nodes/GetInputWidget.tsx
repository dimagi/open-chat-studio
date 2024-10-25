import {
  HistoryNameWidget,
  HistoryTypeWidget,
  KeywordsWidget,
  LlmModelWidget,
  LlmProviderIdWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  ExpandableTextWidget,
  InputField,
} from "../widgets";
import React from "react";
import {getCachedData} from "../utils";
import {InputParam} from "../types/nodeInputTypes";
import {NodeParams} from "../types/nodeParams";

type InputWidgetParams = {
  id: string;
  inputParam: InputParam;
  params: NodeParams;
  setParams: React.Dispatch<React.SetStateAction<NodeParams>>;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
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
export const getInputWidget = ({id, inputParam, params, setParams, updateParamValue}: InputWidgetParams) => {
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
    case "LlmProviderId":
      return (
        <InputField label="LLM Provider">
          <LlmProviderIdWidget
            parameterValues={parameterValues}
            inputParam={inputParam}
            value={params[inputParam.name]}
            setParams={setParams}
            id={id}
          />
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
    case "LlmModel":
      return (
        <InputField label="LLM Model">
          <LlmModelWidget
            parameterValues={parameterValues}
            inputParam={inputParam}
            value={params[inputParam.name]}
            onChange={updateParamValue}
            provider={
              Array.isArray(params.llm_provider_id)
                ? params.llm_provider_id.join("")
                : params.llm_provider_id
            }
          />
        </InputField>
      );
    case "NumOutputs":
      return (
        <InputField label="Number of Outputs">
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name]}
            type="number"
            step="1"
            min="1"
            max="10"
          ></input>
        </InputField>
      );
    case "Keywords": {
      const length =
        parseInt(
          Array.isArray(params.num_outputs)
            ? params.num_outputs.join("")
            : params.num_outputs,
        ) || 1;
      return (
        <>
          {Array.from({length: length}, (_, index) => {
            return (
              <KeywordsWidget
                index={index}
                keywords={
                  Array.isArray(params.keywords) ? params["keywords"] : []
                }
                setParams={setParams}
                id={id}
                key={`${inputParam.name}-${index}`}
              ></KeywordsWidget>
            );
          })}
        </>
      );
    }
    case "HistoryType": {
      return (
        <InputField label="History Type">
          <HistoryTypeWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></HistoryTypeWidget>
        </InputField>
      );
    }
    case "HistoryName": {
      if (params["history_type"] !== "named") {
        return <></>;
      }
      return (
        <InputField label="History Name">
          <HistoryNameWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></HistoryNameWidget>
        </InputField>
      );
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
    case "Prompt": {
      return (
        <ExpandableTextWidget
          humanName={"Prompt"}
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
