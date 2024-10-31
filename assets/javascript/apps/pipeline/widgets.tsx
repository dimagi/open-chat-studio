import React, {
  ChangeEvent,
  ChangeEventHandler,
  ReactNode,
  useId,
} from "react";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParameterValues } from "./types/nodeParameterValues";
import usePipelineStore from "./stores/pipelineStore";
import { NodeProps } from "reactflow";
import {concatenate} from "./utils";
import {NodeParams} from "./types/nodeParams";
import {Node} from "reactflow";

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
  help_text
}: {
  humanName: string;
  name: string;
  value: string | string[];
  help_text: string;
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
    <InputField label={label} help_text={help_text}>
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

export function KeywordsWidget({nodeId, params}: {nodeId: string, params: NodeParams}) {
  const setNode = usePipelineStore((state) => state.setNode);

  function getNewNodeData(old: Node, keywords: any[], numOutputs: number) {
    return {
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          ["keywords"]: keywords,
          ["num_outputs"]: numOutputs,
        },
      },
    };
  }

  const addKeyword = () => {
    setNode(nodeId, (old) => {
      const updatedList = [...(old.data.params["keywords"] || []), ""];
      return getNewNodeData(old, updatedList, old.data.params.num_outputs + 1);
    });
  }

  const updateKeyword = (index: number, value: string) => {
    setNode(nodeId, (old) => {
      const updatedList = [...(old.data.params["keywords"] || [])];
      updatedList[index] = value;
      return getNewNodeData(old, updatedList, old.data.params.num_outputs);
      }
    );
  };

  const deleteKeyword = (index: number) => {
    setNode(nodeId, (old) => {
      const updatedList = [...(old.data.params["keywords"] || [])];
      updatedList.splice(index, 1);
      return getNewNodeData(old, updatedList, old.data.params.num_outputs - 1);
    });
  }

  const length =parseInt(concatenate(params.num_outputs)) || 1;
  const keywords = Array.isArray(params.keywords) ? params["keywords"] : []
  return (
    <>
      <div className="form-control w-full capitalize">
        <label className="label font-bold">
          Outputs
          <div className="tooltip tooltip-left" data-tip="Add Keyword">
            <button className="btn btn-xs btn-ghost" onClick={() => addKeyword()}>
              <i className="fa-solid fa-plus"></i>
            </button>
          </div>
        </label>
      </div>
      <div className="ml-2">
        {Array.from({length: length}, (_, index) => {
          const value = keywords ? keywords[index] || "" : "";
          const label = (
            <>{`Output Keyword ${index + 1}`}
              <div className="tooltip tooltip-left" data-tip={`Delete Keyword ${index + 1}`}>
                <button className="btn btn-xs btn-ghost" onClick={() => deleteKeyword(index)}>
                  <i className="fa-solid fa-minus"></i>
                </button>
              </div>
            </>
          )
          return (
            <div className="form-control w-full capitalize" key={index}>
              <label className="label">{label}</label>
              <input
                className="input input-bordered w-full"
                name="keywords"
                onChange={(event) => updateKeyword(index, event.target.value)}
                value={value}
              ></input>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function LlmWidget({
                            id,
                            parameterValues,
                            inputParam,
                            providerId,
                            model,
                          }: {
  id: NodeProps["id"];
  parameterValues: NodeParameterValues;
  inputParam: InputParam;
  providerId: string;
  model: string;
}) {
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const { value } = event.target;
    const [providerId, model] = value.split('|:|');
    setNode(id, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          llm_provider_id: providerId,
          llm_model: model,
        },
      },
    }));
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
  help_text,
  onChange,
}: {
  inputParam: InputParam;
  historyType: string;
  historyName: string;
  help_text: string;
  onChange: ChangeEventHandler;
}) {
  return (
    <div className="flex join">
      <InputField label="History" help_text={help_text}>
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
        <InputField label="History Name" help_text={help_text}>
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

export function InputField({label, help_text, children}: React.PropsWithChildren<{ label: string | ReactNode, help_text: string }>) {
  return (
    <>
      <div className="form-control w-full capitalize">
        <label className="label font-bold">{label}</label>
        {children}
      </div>
      <small className="text-muted">{help_text}</small>
    </>
  );
}
