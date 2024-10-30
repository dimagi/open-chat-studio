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
  humanName,
  name,
  value,
  onChange,
}: {
  humanName: string;
  name: string;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  const modalId = useId();
  return (
    <>
      <dialog
        id={modalId}
        className="modal nopan nodelete nodrag noflow nowheel"
      >
              <div className="modal-box  min-w-[85vw] h-[80vh] flex flex-col">
          <form method="dialog">
            <button className="btn btn-sm btn-circle btn-ghost absolute right-2 top-2">
              ✕
            </button>
          </form>
          <div className="flex-grow h-full w-full">
            <h4 className="mb-4 font-bold text-lg bottom-2 capitalize">
              {humanName}
            </h4>
            <textarea
              className="textarea textarea-bordered textarea-lg h-[80%] w-full"
              name={name}
              onChange={onChange}
              value={value}
            ></textarea>
            <form method="dialog" className="modal-backdrop">
              <button className="pg-button-primary mt-2">Save</button>
            </form>
          </div>
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
        <i className="fa-solid fa-expand-alt"></i>
      </button>
    </>
  );
}

export function TextWidget({
  humanName,
  name,
  onChange,
  value,
}: {
  humanName: string;
  name: string;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
    <div className="join">
      <textarea
        className="input input-bordered join-item nopan nodelete nodrag noflow textarea nowheel w-full resize-none"
        name={name}
        onChange={onChange}
        value={value}
      ></textarea>
      <div className="join-item">
        <TextModal
          humanName={humanName}
          name={name}
          value={value}
          onChange={onChange}
        ></TextModal>
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
  const humanName = `Output ${index + 1} Keyword`;
  return (
    <>
      <div className="m-1 font-medium text-center">{humanName}</div>
      <TextWidget
        humanName={humanName}
        name="keywords"
        onChange={updateParamValue}
        value={keywords ? keywords[index] : ""}
      ></TextWidget>
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

export function LlmProviderModelWidget({
    parameterValues,
    inputParam,
    value,
    onChange,
    providerId,
}: {
    parameterValues: NodeParameterValues;
    inputParam: InputParam;
    value: string | string[];
    onChange: ChangeEventHandler;
    providerId: string;
}) {

    const providerTypeById = parameterValues.LlmProviderId.reduce((acc, prov) => {
        acc[prov.id] = prov.type;
        return acc;
    }, {});
  const providerType = providerTypeById[providerId];

  const providerModelsByType = parameterValues.LlmProviderModelId.reduce((acc, provModel) => {
      if (!acc[provModel.type]) {
          acc[provModel.type] = [];
      }
      acc[provModel.type].push(provModel);
      return acc;
  }, {});

    return (
        <select
            className="select select-bordered w-full"
            name={inputParam.name}
            onChange={onChange}
            value={value}
        >
            <option value="" disabled>
                Select a provider model
            </option>
            { providerId &&
                providerModelsByType[providerType].map((model) => (
                    <option key={model.id} value={model.id}>
                        {model.name}
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
      <option value="none">No History</option>
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
