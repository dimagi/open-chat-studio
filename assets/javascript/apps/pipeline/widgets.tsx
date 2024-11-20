import React, {
  ChangeEvent,
  ChangeEventHandler,
  ReactNode,
  useId,
} from "react";
import {TypedOption} from "./types/nodeParameterValues";
import usePipelineStore from "./stores/pipelineStore";
import {NodeProps} from "reactflow";
import {concatenate, getCachedData, getSelectOptions} from "./utils";
import {NodeParams, PropertySchema} from "./types/nodeParams";
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
                                       help_text,
                                       inputError
                                     }: {
  humanName: string;
  name: string;
  value: string | string[];
  help_text: string;
  inputError: string | undefined
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
    <InputField label={label} help_text={help_text} inputError={inputError}>
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

export function KeywordsWidget({nodeId, params, inputError}: {
  nodeId: string,
  params: NodeParams,
  inputError?: string | undefined
}) {
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

  const length = parseInt(concatenate(params.num_outputs)) || 1;
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
        <small className="text-red-500">{inputError}</small>
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
                            name,
                            nodeId,
                            providerId,
                            providerModelId,
                          }: {
  name: string;
  nodeId: NodeProps["id"];
  providerId: string;
  providerModelId: string;
}) {
  const {parameterValues} = getCachedData();
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const {value} = event.target;
    const [providerId, providerModelId] = value.split('|:|');
    setNode(nodeId, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          llm_provider_id: providerId,
          llm_provider_model_id: providerModelId,
        },
      },
    }));
  };

  const makeValue = (providerId: string, providerModelId: string) => {
    return providerId + '|:|' + providerModelId;
  };

  type ProviderModelsByType = { [type: string]: TypedOption[] };
  const providerModelsByType = parameterValues.LlmProviderModelId.reduce((acc, provModel) => {
    if (!acc[provModel.type]) {
      acc[provModel.type] = [];
    }
    acc[provModel.type].push(provModel);
    return acc;
  }, {} as ProviderModelsByType);

  return (
    <select
      className="select select-bordered w-full"
      name={name}
      onChange={updateParamValue}
      value={makeValue(providerId, providerModelId)}
    >
      <option value="" disabled>
        Select a model
      </option>
      {parameterValues.LlmProviderId.map((provider) => (
        providerModelsByType[provider.type] &&
        providerModelsByType[provider.type].map((providerModel) => (
          <option key={provider.value + providerModel.value} value={makeValue(provider.value, providerModel.value)}>
            {providerModel.label}
          </option>
        ))
      ))}
    </select>
  );
}


export function HistoryTypeWidget({
                                    name,
                                    historyType,
                                    historyName,
                                    help_text,
                                    onChange,
                                    schema,
                                  }: {
  name: string;
  historyType: string;
  historyName: string;
  help_text: string;
  onChange: ChangeEventHandler;
  schema: PropertySchema
}) {
  const options = getSelectOptions(schema);
  return (
    <div className="flex join">
      <InputField label="History" help_text={help_text}>
        <select
          className="select select-bordered join-item"
          name={name}
          onChange={onChange}
          value={historyType}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
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

export function InputField({label, help_text, inputError, children}: React.PropsWithChildren<{
  label: string | ReactNode,
  help_text: string,
  inputError?: string | undefined
}>) {
  return (
    <>
      <div className="form-control w-full capitalize">
        <label className="label font-bold">{label}</label>
        {children}
      </div>
      <div className="flex flex-col">
        <small className="text-red-500">{inputError}</small>
        <small className="text-muted">{help_text}</small>
      </div>
    </>
  );
}
