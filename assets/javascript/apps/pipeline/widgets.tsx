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

export function KeywordsWidget({
  index,
  keywords,
  setParams,
  id,
}: {
  index: number;
  keywords: string[];
  setParams: Dispatch<SetStateAction<NodeParams>>;
  id: NodeProps["id"];
}) {
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setParams((prevParams) => {
      const { name, value } = event.target;
      const updatedList = [...(prevParams[name] || [])];
      updatedList[index] = value;
      const newParams = { ...prevParams, [name]: updatedList };
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
    <>
      <div className="m-1 font-medium text-center">
        {`Keyword ${index + 1}`}
      </div>
      <textarea
        className="textarea textarea-bordered w-full"
        name="keywords"
        onChange={updateParamValue}
        value={keywords ? keywords[index] : ""}
      ></textarea>
    </>
  );
}

export function LlmProviderIdWidget({
  parameterValues,
  inputParam,
  value,
  setParams,
  id,
}: {
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  value: string | string[];
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
  value: string | string[];
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

export function SourceMaterialIdWidget({
  parameterValues,
  inputParam,
  value,
  onChange,
}: {
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
    >
      <option value="">Select a topic</option>
      {parameterValues.SourceMaterialId.map((material) => (
        <option key={material["id"]} value={material["id"]}>
          {material["topic"]}
        </option>
      ))}
    </select>
  );
}

export function HistoryTypeWidget({
  inputParam,
  value,
  onChange,
}: {
  inputParam: InputParam;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
    >
      <option value="node">Node</option>
      <option value="global">Global</option>
      <option value="named">Named</option>
    </select>
  );
}

export function HistoryNameWidget({
  inputParam,
  value,
  onChange,
}: {
  inputParam: InputParam;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <textarea
      className="textarea textarea-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
    ></textarea>
  );
}

export function MaxTokenLimitWidget({
  inputParam,
  value,
  onChange,
}: {
  inputParam: InputParam;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <input
      className="input input-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
      type="number"
      step="1"
    ></input>
  );
}
