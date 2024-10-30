import {
  HistoryNameWidget,
  HistoryTypeWidget,
  KeywordsWidget,
  LlmProviderIdWidget,
  LlmProviderModelWidget,
  MaxTokenLimitWidget,
  SourceMaterialIdWidget,
  TextWidget
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
  if (inputParam.name == "llm_model" || inputParam.name == "max_token_limit"){
    /*
       This is here as we migrated llm_model to llm_provider_model_id, in October 2024.
       During the migration, we kept the data in llm_model as a safeguard. This check can safely be deleted once a second migration to delete all instances of llm_model has been run.
       TODO: Remove this check once there are no instances of llm_model or max_token_limit in the node definitions.
     */
    return
  }
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
      case "LlmProviderModelId":
          return (
              <>
                  <div className="m-1 font-medium text-center">LLM Model</div>
                  <LlmProviderModelWidget
                      parameterValues={parameterValues}
                      inputParam={inputParam}
                      value={params[inputParam.name]}
                      onChange={updateParamValue}
                      providerId={
                          Array.isArray(params.llm_provider_id)
                              ? params.llm_provider_id.join("")
                              : params.llm_provider_id
                      }
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
    case "NumOutputs":
      return (
        <>
          <div className="m-1 font-medium text-center">Number of Outputs</div>
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
