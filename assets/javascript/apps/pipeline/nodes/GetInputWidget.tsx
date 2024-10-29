import {
  HistoryTypeWidget,
  KeywordsWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  ExpandableTextWidget,
  InputField, LlmWidget,
} from "../widgets";
import React from "react";
import {getCachedData, join} from "../utils";
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
            setParams={setParams}
            providerId={join(params.llm_provider_id)}
            model={join(params.llm_model)}
            ></LlmWidget>
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
      const length =parseInt(join(params.num_outputs)) || 1;
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
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name]}
          ></input>
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
