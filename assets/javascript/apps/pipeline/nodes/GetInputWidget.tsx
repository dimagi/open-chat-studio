import {
  HistoryNameWidget,
  HistoryTypeWidget,
  KeywordsWidget,
  LlmModelWidget,
  LlmProviderIdWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  TextWidget
} from "../widgets";
import React from "react";
import {getCachedData} from "../utils";
import {InputParam} from "../types/nodeInputTypes";
import {NodeParams} from "../types/nodeParams";

interface InputWidgetParams {
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
        <>
          <div className="m-1 font-medium text-center">Temperature</div>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name]}
            type="number"
            step=".1"
          ></input>
        </>
      );
    case "LlmProviderId":
      return (
        <>
          <div className="m-1 font-medium text-center">LLM Provider</div>
          <LlmProviderIdWidget
            parameterValues={parameterValues}
            inputParam={inputParam}
            value={params[inputParam.name]}
            setParams={setParams}
            id={id}
          />
        </>
      );
    case "SourceMaterialId":
      return (
        <>
          <div className="m-1 font-medium text-center">Source Material</div>
          <SourceMaterialIdWidget
            parameterValues={parameterValues}
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          />
        </>
      );
    case "LlmModel":
      return (
        <>
          <div className="m-1 font-medium text-center">LLM Model</div>
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
        </>
      );
    case "NumOutputs":
      return (
        <>
          <div className="m-1 font-medium text-center">Number of Outputs</div>
          <input
            className="input input-bordered w-full"
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name] || 1}
            type="number"
            step="1"
          ></input>
        </>
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
        <>
          <div className="m-1 font-medium text-center">History Type</div>
          <HistoryTypeWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></HistoryTypeWidget>
        </>
      );
    }
    case "HistoryName": {
      if (params["history_type"] !== "named") {
        return <></>;
      }
      return (
        <>
          <div className="m-1 font-medium text-center">History Name</div>
          <HistoryNameWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></HistoryNameWidget>
        </>
      );
    }
    case "MaxTokenLimit": {
      if (params["history_type"] !== "global") {
        return <></>;
      }
      return (
        <>
          <div className="m-1 font-medium text-center">
            Maximum Token Limit
          </div>
          <MaxTokenLimitWidget
            onChange={updateParamValue}
            inputParam={inputParam}
            value={params[inputParam.name]}
          ></MaxTokenLimitWidget>
        </>
      );
    }
    default: {
      const humanName = inputParam.human_name
        ? inputParam.human_name
        : inputParam.name.replace(/_/g, " ");
      return (
        <>
          <div className="m-1 font-medium text-center capitalize">
            {humanName}
          </div>
          <TextWidget
            humanName={humanName}
            name={inputParam.name}
            onChange={updateParamValue}
            value={params[inputParam.name] || ""}
          ></TextWidget>
        </>
      );
    }
  }
};
