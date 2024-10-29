import React, {
  ChangeEvent,
  ChangeEventHandler,
  Dispatch, ReactNode,
  SetStateAction,
  useId,
} from "react";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParameterValues } from "./types/nodeParameterValues";
import usePipelineStore from "./stores/pipelineStore";
import { NodeParams } from "./types/nodeParams";
import { NodeProps } from "reactflow";

export function TextModal({
  modalId,
  humanName,
  name,
  value,
  onChange,
}: {
  modalId: string;
  humanName: string;
  name: string;
  value: string | string[];
  onChange: ChangeEventHandler;
}) {
  return (
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
        <div className="flex-grow h-full w-full flex flex-col">
          <h4 className="mb-4 font-bold text-lg bottom-2 capitalize">
            {humanName}
          </h4>
          <textarea
            className="textarea textarea-bordered textarea-lg w-full flex-grow resize-none"
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
  );
}

export function ExpandableTextWidget({
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
  const modalId = useId();
  const openModal = () => (document.getElementById(modalId) as HTMLDialogElement)?.showModal()
  const label = (
    <>{humanName}
      <div className="tooltip tooltip-left" data-tip={`Expand ${humanName}`}>
        <button className="btn btn-xs btn-ghost" onClick={openModal}>
          <i className="fa-solid fa-expand-alt"></i>
        </button>
      </div>
    </>
  )
  return (
    <InputField label={label}>
      <textarea
        className="textarea textarea-bordered resize-none textarea-sm w-full"
        rows={3}
        name={name}
        onChange={onChange}
        value={value}
      ></textarea>
      <TextModal
        modalId={modalId}
        humanName={humanName}
        name={name}
        value={value}
        onChange={onChange}>
      </TextModal>
    </InputField>
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
  const updateParamValue = (event: ChangeEvent<HTMLInputElement>) => {
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
    <InputField label={humanName}>
      <input
        className="input input-bordered w-full"
        name={humanName}
        onChange={updateParamValue}
        value={keywords ? keywords[index] : ""}
      ></input>
    </InputField>
  );
}

export function LlmWidget({
  id,
  parameterValues,
  inputParam,
  setParams,
  providerId,
  model,
}: {
  id: NodeProps["id"];
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  setParams: Dispatch<SetStateAction<NodeParams>>;
  providerId: string;
  model: string;
}) {
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const { value } = event.target;
    const [providerId, model] = value.split('|:|');
    setParams((prevParams) => {
      const newParams = {
        ...prevParams,
        llm_provider_id: providerId,
        llm_model: model,
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

  const makeValue = (providerId: string, model: string) => {
    return providerId + '|:|' + model;
  }
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={updateParamValue}
      value={makeValue(providerId, model)}
    >
      <option value="" disabled>
        Select a model
      </option>
      {parameterValues.LlmProviderId.map((provider) => (
        parameterValues.LlmModel[provider.id] &&
        parameterValues.LlmModel[provider.id].map((model) => (
          <option key={provider.id + model} value={makeValue(provider.id, model)}>
            {provider.name}: {model}
          </option>
        ))
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
  historyType,
  historyName,
  onChange,
}: {
  inputParam: InputParam;
  historyType: string;
  historyName: string;
  onChange: ChangeEventHandler;
}) {
  return (
    <div className="flex join">
      <InputField label="History">
        <select
          className="select select-bordered join-item"
          name={inputParam.name}
          onChange={onChange}
          value={historyType}
        >
          <option value="none">No History</option>
          <option value="node">Node</option>
          <option value="global">Global</option>
          <option value="named">Named</option>
        </select>
      </InputField>
      {historyType == "named" && (
        <InputField label="History Name">
          <input
            className="input input-bordered join-item"
            name="history_name"
            onChange={onChange}
            value={historyName || ""}
          ></input>
        </InputField>
      )}
    </div>
)
  ;
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

export function InputField({label, children}: React.PropsWithChildren<{ label: string | ReactNode }>) {
  return (
    <>
      <div className="form-control w-full capitalize">
        <label className="label font-bold">{label}</label>
        {children}
      </div>
    </>
  );
}
