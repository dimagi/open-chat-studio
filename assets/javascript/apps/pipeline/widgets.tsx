import React, {
  ChangeEvent,
  ChangeEventHandler,
  Dispatch,
  SetStateAction,
  useId,
} from "react";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParameterValues } from "./types/nodeParameterValues";
import usePipelineStore from "./stores/pipelineStore";
import { NodeParams } from "./types/nodeParams";
import { NodeProps } from "reactflow";

export function TextModal({
  name,
  value,
  onChange,
}: {
  name: string;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  const modalId = useId();
  return (
    <>
      <dialog id={modalId} className="modal">
        <div className="modal-box">
          <form method="dialog">
            <button className="btn btn-sm btn-circle btn-ghost absolute right-2 top-2">
              ✕
            </button>
          </form>
          <h4 className="font-bold text-lg">Edit "{name}"</h4>
          <textarea
            className="textarea textarea-bordered textarea-md w-full"
            name={name}
            onChange={onChange}
            value={value}
          ></textarea>
          <form method="dialog" className="modal-backdrop">
            <button className="pg-button-primary mt-2">Save</button>
          </form>
        </div>
        <form method="dialog" className="modal-backdrop">
          {/* Allows closing the modal by clicking outside of it */}
          <button>close</button>
        </form>
      </dialog>
      <button
        className="btn btn-ghost"
        onClick={() =>
          (document.getElementById(modalId) as HTMLDialogElement)?.showModal()
        }
      >
        <i className="fa-solid fa-pencil"></i>
      </button>
    </>
  );
}

export function PromptWidget({
  name,
  onChange,
  value,
}: {
  name: string;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <div className="join">
      <input
        className="w-full input input-bordered join-item"
        name={name}
        onChange={onChange}
        value={value}
        type="text"
        disabled
      ></input>
      <div className="join-item">
        <TextModal name={name} value={value} onChange={onChange}></TextModal>
      </div>
    </div>
  );
}

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
