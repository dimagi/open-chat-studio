import React, {
  ChangeEvent,
  ChangeEventHandler,
  Dispatch,
  SetStateAction,
} from "react";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParameterValues } from "./types/nodeParameterValues";
import usePipelineStore from "./stores/pipelineStore";
import { NodeParams } from "./types/nodeParams";
import { NodeProps } from "reactflow";

export function LlmProviderIdWidget({
  parameterValues,
  inputParam,
  value,
  setParams,
  id,
}: {
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  value: string;
  setParams: Dispatch<SetStateAction<NodeParams>>;
  id: NodeProps["id"];
}) {
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const { value } = event.target;
    setParams((prevParams) => {
      const newParams = {
        ...prevParams,
        llm_provider_id: value,
        llm_model: "",
      };
      setNode(id, (old) => ({
        ...old,
        data: {
          ...old.data,
          params: newParams,
        },
      }));
      return newParams;
    });
  };
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={updateParamValue}
      value={value}
    >
      <option value="" disabled>
        Select a provider
      </option>
      {parameterValues.LlmProviderId.map((opt) => (
        <option key={opt.id} value={opt.id}>
          {opt.name}
        </option>
      ))}
    </select>
  );
}

export function LlmModelWidget({
  parameterValues,
  inputParam,
  value,
  onChange,
  provider,
}: {
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  value: string;
  onChange: ChangeEventHandler;
  provider: string;
}) {
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
    >
      <option value="" disabled>
        Select a model
      </option>
      {parameterValues.LlmModel[provider] &&
        parameterValues.LlmModel[provider].map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
    </select>
  );
}
