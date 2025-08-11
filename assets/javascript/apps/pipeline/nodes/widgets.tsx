import React, {ChangeEvent, ChangeEventHandler, ReactNode, useId, useState,} from "react";
import {LlmProviderModel, Option, TypedOption} from "../types/nodeParameterValues";
import usePipelineStore from "../stores/pipelineStore";
import {classNames, concatenate, getCachedData, getDocumentationLink, getSelectOptions} from "../utils";
import {JsonSchema, NodeParams, PropertySchema} from "../types/nodeParams";
import {Node, useUpdateNodeInternals} from "reactflow";
import DOMPurify from 'dompurify';
import {apiClient} from "../api/api";
import {produce} from "immer";
import {CodeNodeEditor, PromptEditor} from "../components/CodeMirrorEditor";


export function getWidget(name: string, params: PropertySchema) {
  switch (name) {
    case "toggle":
      return ToggleWidget
    case "float":
      return FloatWidget
    case "range":
      return RangeWidget
    case "expandable_text":
      return ExpandableTextWidget
    case "code":
      return CodeWidget
    case "select":
      return SelectWidget
    case "multiselect":
      return MultiSelectWidget
    case "llm_provider_model":
      return LlmWidget
    case "history":
      return HistoryTypeWidget
    case "history_mode":
      return HistoryModeWidget
    case "keywords":
      return KeywordsWidget
    case "node_name":
      return NodeNameWidget
    case "built_in_tools":
        return BuiltInToolsWidget
    case "text_editor_widget":
        return TextEditorWidget
    case "voice_widget":
        return VoiceWidget
    default:
      if (params.enum) {
        return SelectWidget
      }
      return DefaultWidget
  }
}

interface WidgetParams {
  nodeId: string;
  name: string;
  label: string;
  helpText: string;
  paramValue: string | string[];
  inputError: string | undefined;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
  schema: PropertySchema
  nodeParams: NodeParams
  nodeSchema: JsonSchema
  required: boolean,
  getNodeFieldError: (nodeId: string, fieldName: string) => string | undefined;
  readOnly: boolean,
}

interface ToggleWidgetParams extends Omit<WidgetParams, 'paramValue'> {
  paramValue: boolean;
}


function DefaultWidget(props: WidgetParams) {
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <input
        className="input w-full"
        name={props.name}
        onChange={props.updateParamValue}
        value={props.paramValue}
        type="text"
        required={props.required}
        readOnly={props.readOnly}
      ></input>
    </InputField>
  );
}

/**
 * A widget component for displaying and editing the name of a node.
 *
 * Will display a blank input field if the current value matches the node ID.
 */
function NodeNameWidget(props: WidgetParams) {
  const value = concatenate(props.paramValue);
  const [inputValue, setInputValue] = React.useState(value === props.nodeId ? "" : value);

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(event.target.value);
    if (!event.target.value) {
      event.target.value = props.nodeId;
    }
    props.updateParamValue(event);
  };

  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <input
        className="input w-full"
        name={props.name}
        onChange={handleInputChange}
        value={inputValue}
        type="text"
        required={props.required}
        readOnly={props.readOnly}
      ></input>
    </InputField>
  );
}

function FloatWidget(props: WidgetParams) {
  return <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
    <input
      className="input w-full"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="number"
      step=".1"
      required={props.required}
      readOnly={props.readOnly}
    ></input>
  </InputField>
}

function RangeWidget(props: WidgetParams) {
  const getPropOrOther = (prop: string, other: string) => {
    const val = props.schema[prop];
    if (val !== undefined) {
      return val;
    }
    return props.schema[other];
  }
  return <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
    <input
      className="input w-full input-sm"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="number"
      step=".1"
      required={props.required}
      readOnly={props.readOnly}
    ></input>
    <input
      className="range range-xs w-full"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="range"
      min={getPropOrOther("minimum", "exclusiveMinimum")}
      max={getPropOrOther("maximum", "exclusiveMaximum")}
      step=".1"
      required={props.required}
      disabled={props.readOnly}
    ></input>
  </InputField>
}

function ToggleWidget(props: ToggleWidgetParams) {
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <input
        className="toggle"
        name={props.name}
        onChange={props.updateParamValue}
        checked={props.paramValue}
        type="checkbox"
        disabled={props.readOnly}
      ></input>
    </InputField>
  );
}

function SelectWidget(props: WidgetParams) {
  const options = getSelectOptions(props.schema);
  const selectedOption = options.find((option) => option.value.toString() === props.paramValue);
  const [link, setLink] = useState<string | undefined>(selectedOption?.edit_url);

  const onUpdate = (event: ChangeEvent<HTMLSelectElement>) => {
    const selectedOption = options.find((option) => option.value.toString() === event.target.value);
    setLink(selectedOption?.edit_url);
    props.updateParamValue(event);
  };


  return <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
    <div className="flex flex-row gap-2">
      <select
        className="select w-full"
        name={props.name}
        onChange={onUpdate}
        value={props.paramValue}
        required={props.required}
        disabled={props.readOnly}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {link && (
        <div className="tooltip" data-tip="Open in a new tab">
          <a target="_blank" href={DOMPurify.sanitize(link)} className="align-bottom hover:cursor-pointer">
            <i className="fa-solid fa-up-right-from-square fa-lg"></i>
          </a>
        </div>
      )}
    </div>
  </InputField>
}


function MultiSelectWidget(props: WidgetParams) {
  const options = getSelectOptions(props.schema);
  if (options.length == 0) {
    return <></>
  }
  // props.paramValue is made immutable when produce is used to update the node, so we have to copy props.paramValue
  // in order to push to it
  let selectedValues = Array.isArray(props.paramValue) ? [...props.paramValue] : [];

  const setNode = usePipelineStore((state) => state.setNode);

  function getNewNodeData(old: Node, updatedList: Array<string>) {
    return produce(old, next => {
      next.data.params[props.name] = updatedList;
    });
  }

  function onUpdate(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.checked) {
      selectedValues.push(event.target.name)
    } else {
      selectedValues = selectedValues.filter((tool) => tool !== event.target.name)
    }
    setNode(props.nodeId, (old) => {
      return getNewNodeData(old, selectedValues);
    }
    );
  };

  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      {options.map((option) => (
        <div className="flex items-center mb-1" key={option.value}>
          <input
            className="checkbox"
            name={option.value}
            onChange={onUpdate}
            checked={selectedValues.includes(option.value)}
            id={option.value}
            key={option.value}
            type="checkbox"
            disabled={props.readOnly}
          />
          <span className="ml-2">{option.label}</span>
        </div>
      ))}
    </InputField>
  )
}

export function CodeWidget(props: WidgetParams) {
  const setNode = usePipelineStore((state) => state.setNode);
  const onChangeCallback = (value: string) => {
    setNode(props.nodeId, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          [props.name]: value,
        },
      },
    }));
  };

  const modalId = useId();
  const openModal = () => (document.getElementById(modalId) as HTMLDialogElement)?.showModal()
  const label = (
    <>
      {props.label}
      <div className="tooltip tooltip-left" data-tip={`Expand ${props.label}`}>
        <button className="btn btn-xs btn-ghost float-right" onClick={openModal}>
          <i className="fa-solid fa-expand-alt"></i>
        </button>
      </div >
    </>
  )
  return (
    <>
      <InputField label={label} help_text={props.helpText} inputError={props.inputError}>
        <div className="relative w-full">
          <textarea
            className="textarea textarea-bordered resize-none textarea-sm w-full overflow-x-auto overflow-y"
            readOnly={true}
            rows={3}
            wrap="off"
            name={props.name}
            value={props.paramValue}
          ></textarea>
          <div
            className="absolute inset-0 cursor-pointer"
            onClick={openModal}
          ></div>
        </div>
      </InputField>
      <CodeModal
        modalId={modalId}
        humanName={props.label}
        value={concatenate(props.paramValue)}
        onChange={onChangeCallback}
        inputError={props.inputError}
        documentationLink={getDocumentationLink(props.nodeSchema)}
        readOnly={props.readOnly}
      />
    </>
  );
}

export function CodeModal(
  { modalId, humanName, value, onChange, inputError, documentationLink, readOnly }: {
    modalId: string;
    humanName: string;
    value: string;
    onChange: (value: string) => void;
    inputError: string | undefined;
    documentationLink: string | null;
    readOnly: boolean;
  }) {

  const [showGenerate, setShowGenerate] = useState(false);

  return (
    <dialog
      id={modalId}
      className="modal nopan nodelete nodrag noflow nowheel"
      onClose={() => setShowGenerate(false)}
    >
      <div className="modal-box  min-w-[85vw] h-[80vh] flex flex-col">
        <form method="dialog">
          <button className="btn btn-sm btn-circle btn-ghost absolute right-2 top-2">
            ✕
          </button>
        </form>
        <div className="grow h-full w-full flex flex-col">
          <div className="flex justify-between items-center">
            <h4 className="font-bold text-lg bottom-2 capitalize">
              {humanName}
              {documentationLink && <a href={documentationLink} target={"_blank"} className="ml-2 font-light text-info tooltip tooltip-right" data-tip="View Documentation">
                <i className="fa-regular fa-circle-question fa-sm"></i>
              </a>}
            </h4>
            {!readOnly && <button className="btn btn-sm btn-ghost" onClick={() => setShowGenerate(!showGenerate)}>
              <i className="fa-solid fa-wand-magic-sparkles"></i>Help
            </button>}
          </div>
          {!readOnly && <GenerateCodeSection
            showGenerate={showGenerate}
            setShowGenerate={setShowGenerate}
            onAccept={onChange}
            currentCode={value}
          />}
          <CodeNodeEditor
            value={value}
            onChange={onChange}
            readOnly={readOnly}
            />
        </div>
        <div className="flex flex-col">
            <span className="text-red-500">{inputError}</span>
        </div>
      </div>
      <form method="dialog" className="modal-backdrop">
        {/* Allows closing the modal by clicking outside of it */}
        <button>close</button>
      </form>
    </dialog>
  );
}

function GenerateCodeSection({
  showGenerate,
  setShowGenerate,
  onAccept,
  currentCode,
}: {
  showGenerate: boolean;
  setShowGenerate: (value: boolean) => void;
  onAccept: (value: string) => void;
  currentCode: string;
}) {
  const [prompt, setPrompt] = useState("")
  const [generated, setGenerated] = useState("")
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState("")

  const generateCode = () => {
    setGenerating(true);
    apiClient.generateCode(prompt, currentCode).then((generatedCode) => {
      setGenerating(false);
      if (generatedCode.error || generatedCode.response === "") {
        setError(generatedCode.error || "No code generated. Please provide more information.");
        return;
      } else if (generatedCode.response) {
        setGenerated(generatedCode.response);
        setShowGenerate(false);
      }
    }).catch(() => {
      setGenerating(false);
      setError("An error occurred while generating code. Please try again.");
    });
  }

  const handleKeydown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.ctrlKey && e.key === "Enter") {
      generateCode();
    }
  }

  return (
    <div>
      {showGenerate && (
        <div className={"my-2"}>
          <textarea
            className="textarea textarea-bordered resize-none textarea-sm w-full"
            rows={2}
            wrap="off"
            placeholder="Describe what you want the Python Node to do or what issue you are facing"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeydown}
          ></textarea>
          {error && <small className="text-red-500">{error}</small>}
          <div className={"flex items-center gap-2"}>
            <button className={"btn btn-sm btn-primary"} onClick={generateCode} disabled={!prompt}>
              <i className="fa-solid fa-wand-magic-sparkles"></i>Go
            </button>
            {generating && <span className="loading loading-bars loading-md"></span>}
          </div>
        </div>
      )}
      {generated &&
        <div>
          <h2 className="font-semibold">Generated Code</h2>
          <CodeNodeEditor
            value={generated}
            onChange={setGenerated}
            readOnly={false}
            />
        <div className={"my-2 join"}>
          <button className={"btn btn-sm btn-success join-item"} onClick={() => {
            onAccept(generated)
            setShowGenerate(false)
            setGenerated("")
            setPrompt("")
          }}>
            <i className="fa-solid fa-check"></i>
            Use Generated Code
          </button>
          <button className={"btn btn-sm btn-warning join-item"} onClick={() => {
            setGenerated("")
            setShowGenerate(true)
          }}>
            <i className="fa-solid fa-arrows-rotate"></i>
            Regenerate
          </button>
        </div>
      </div>
    }
    </div>
  );
}

export function TextModal(
  {modalId, humanName, name, value, onChange, readOnly}: {
    modalId: string;
    humanName: string;
    name: string;
    value: string | string[];
    onChange: ChangeEventHandler;
    readOnly: boolean;
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
        <div className="grow h-full w-full flex flex-col">
          <h4 className="mb-4 font-bold text-lg bottom-2 capitalize">
            {humanName}
          </h4>
          <textarea
            className="textarea textarea-bordered textarea-lg w-full grow resize-none"
            name={name}
            onChange={onChange}
            value={value}
            readOnly={readOnly}
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

export function ExpandableTextWidget(props: WidgetParams) {
  const modalId = useId();
  const openModal = () => (document.getElementById(modalId) as HTMLDialogElement)?.showModal()
  const label = (
    <>{props.label}
      <div className="tooltip tooltip-left" data-tip={`Expand ${props.label}`}>
        <button className="btn btn-xs btn-ghost" onClick={openModal}>
          <i className="fa-solid fa-expand-alt"></i>
        </button>
      </div>
    </>
  )
  return (
    <InputField label={label} help_text={props.helpText} inputError={props.inputError}>
      <textarea
        className="textarea textarea-bordered resize-none textarea-sm w-full"
        rows={3}
        name={props.name}
        onChange={props.updateParamValue}
        value={props.paramValue}
        readOnly={props.readOnly}
      ></textarea>
      <TextModal
        modalId={modalId}
        humanName={props.label}
        name={props.name}
        value={props.paramValue}
        onChange={props.updateParamValue}
        readOnly={props.readOnly}
      >
      </TextModal>
    </InputField>
  );
}

export function KeywordsWidget(props: WidgetParams) {
  const setNode = usePipelineStore((state) => state.setNode);
  const setEdges = usePipelineStore((state) => state.setEdges);
  const updateNodeInternals = useUpdateNodeInternals()

  function getNewNodeData(old: Node, keywords: any[], newDefaultIndex?: number) {
    return produce(old, next => {
      next.data.params["keywords"] = keywords;
      if (newDefaultIndex !== undefined) {
        next.data.params["default_keyword_index"] = newDefaultIndex;
      }
    });
  }

  const addKeyword = () => {
    setNode(props.nodeId, (old) => {
      const updatedList = [...(old.data.params["keywords"] || []), ""];
      return getNewNodeData(old, updatedList);
    });
    updateNodeInternals(props.nodeId);
  }

  const updateKeyword = (index: number, value: string) => {
    setNode(props.nodeId, (old) => {
        const updatedList = [...(old.data.params["keywords"] || [])];
        updatedList[index] = value;
        return getNewNodeData(old, updatedList);
      }
    );
  };

  const deleteKeyword = (index: number) => {
    setNode(props.nodeId, (old) => {
      const updatedList = [...(old.data.params["keywords"] || [])];
      updatedList.splice(index, 1);
      const defaultIndex = old.data.params["default_keyword_index"] || 0;

      let newDefaultIndex = defaultIndex;
      if (index === defaultIndex) {
        newDefaultIndex = 0;
      } else if (index < defaultIndex) {
        newDefaultIndex = defaultIndex - 1;
      }

      return getNewNodeData(old, updatedList, newDefaultIndex);
    });
    updateNodeInternals(props.nodeId);

    const handleName = `output_${index}`;
    setEdges((old) => {
      const edges = old.filter((edge) => {
        // remove edges that have this handle as source
        if (edge.source != props.nodeId) {
          return true;
        }
        return edge.sourceHandle != handleName;
      }).map((edge) => {
        if (edge.source != props.nodeId) {
          return edge;
        }
        const sourceHandleIndex = edge.sourceHandle && +edge.sourceHandle.split("_")[1];
        if (sourceHandleIndex && sourceHandleIndex > index) {
          const newSourceHandle = `output_${sourceHandleIndex - 1}`;
          return {...edge, sourceHandle: newSourceHandle}
        }
        return edge;
      });
      return edges;
    });
  }

  const setAsDefault = (index: number) => {
    setNode(props.nodeId, (old) => {
      return getNewNodeData(old, [...(old.data.params["keywords"] || [])], index);
    });
  }

  const length = (Array.isArray(props.nodeParams.keywords) ? props.nodeParams.keywords.length : 1);
  const keywords = Array.isArray(props.nodeParams.keywords) ? props.nodeParams["keywords"] : [];
  const defaultIndex = props.nodeParams.default_keyword_index;
  const canDelete = length > 1;

  return (
    <>
      <div className="fieldset w-full capitalize">
        <label className="label font-bold">
          Outputs
          <div className="tooltip tooltip-left" data-tip="Add Keyword">
            <button className="btn btn-xs btn-ghost" onClick={() => addKeyword()}>
              <i className="fa-solid fa-plus"></i>
            </button>
          </div>
        </label>
        <small className="text-red-500">{props.inputError}</small>
      </div>
      <div className="ml-2">
        {Array.from({length: length}, (_, index) => {
          const value = keywords ? keywords[index] || "" : "";
          const label = `Output Keyword ${index + 1}`;
          const isDefault = index === defaultIndex;

          return (
            <div className="fieldset w-full capitalize" key={index}>
              <div className="flex justify-between items-center">
                <label className="label">
                  {label}
                  <div className="pl-2 tooltip" data-tip={isDefault ? "Default" : "Set as Default"}>
                    <span
                      onClick={() => !props.readOnly && !isDefault && setAsDefault(index)}
                      style={{ cursor: isDefault ? 'default' : 'pointer' }}
                    >
                      {isDefault ? (
                        <i className="fa-solid fa-star text-accent"></i>
                      ) : (
                        <i className="fa-regular fa-star text-gray-500"></i>
                      )}
                    </span>
                  </div>
                </label>
                {!props.readOnly && <div className="tooltip tooltip-left" data-tip={`Delete Keyword ${index + 1}`}>
                  <button className="btn btn-xs btn-ghost" onClick={() => deleteKeyword(index)} disabled={!canDelete}>
                    <i className="fa-solid fa-minus"></i>
                  </button>
                </div>}
              </div>
              <input
                className={classNames("input w-full", value ? "" : "input-error")}
                name="keywords"
                onChange={(event) => updateKeyword(index, event.target.value)}
                value={value}
                readOnly={props.readOnly}
              ></input>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function LlmWidget(props: WidgetParams) {

  const {parameterValues} = getCachedData();
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const {value} = event.target;
    const [providerId, providerModelId] = value.split('|:|');
    setNode(props.nodeId, (old) =>
      produce(old, (next) => {
        next.data.params.llm_provider_id = providerId;
        next.data.params.llm_provider_model_id = providerModelId;
      })
    );
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

  const providerId = concatenate(props.nodeParams.llm_provider_id);
  const providerModelId = concatenate(props.nodeParams.llm_provider_model_id);
  const value = makeValue(providerId, providerModelId)
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <select
        className="select w-full"
        name={props.name}
        onChange={updateParamValue}
        value={value}
        disabled={props.readOnly}
      >
        <option value="" disabled>
          Select a model
        </option>
        {parameterValues.LlmProviderId.map((provider) => {
          const providersWithSameType = parameterValues.LlmProviderId.filter(p => p.type === provider.type).length;

          return providerModelsByType[provider.type] &&
            providerModelsByType[provider.type].map((providerModel) => (
              <option key={provider.value + providerModel.value} value={makeValue(provider.value, providerModel.value)}>
                {providerModel.label}{providersWithSameType > 1 ? ` (${provider.label})` : ''}
              </option>
            ))
        })}
      </select>
    </InputField>
  );
}

export function HistoryTypeWidget(props: WidgetParams) {
  const options = getSelectOptions(props.schema);
  const historyType = concatenate(props.paramValue);
  const historyName = concatenate(props.nodeParams["history_name"]);
  const historyNameError = props.getNodeFieldError(props.nodeId, "history_name");

  return (
    <>
      <div className="flex join">
        <InputField label="History" help_text={props.helpText}>
          <select
            className={`select join-item ${historyType == 'named' ? '' : 'w-full'}`}
            name={props.name}
            onChange={props.updateParamValue}
            value={historyType}
            disabled={props.readOnly}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </InputField>
        {historyType == "named" && (
          <InputField label="History Name" help_text={props.helpText}>
            <input
              className="input join-item"
              name="history_name"
              onChange={props.updateParamValue}
              value={historyName || ""}
              readOnly={props.readOnly}
            ></input>
          </InputField>
        )}
      </div>
      <div className="flex flex-col">
        <small className="text-red-500">{historyNameError}</small>
      </div>
    </>
  );
}

export function HistoryModeWidget(props: WidgetParams) {
  const options = getSelectOptions(props.schema);
  const userMaxTokenLimit = concatenate(props.nodeParams["user_max_token_limit"]);
  const maxHistoryLength = concatenate(props.nodeParams["max_history_length"]);
  const initialHistoryMode = concatenate(props.nodeParams["history_mode"]);
  const [historyMode, setHistoryMode] = useState(initialHistoryMode || "summarize");
  const llmProviderId = concatenate(props.nodeParams["llm_provider_model_id"]);
  const {parameterValues} = getCachedData();
  const models = parameterValues.LlmProviderModelId as LlmProviderModel[];
  const model = models.filter(m => String(m.value) === String(llmProviderId));
  const defaultMaxTokens = model.length > 0 && model[0].max_token_limit !== undefined ? model[0].max_token_limit : 0;
  const historyModeHelpTexts: Record<string, string> = {
    summarize:"If the token count exceeds the limit, older messages will be summarized while keeping the last few messages intact.",
    truncate_tokens:"If the token count exceeds the limit, older messages will be removed until the token count is below the limit.",
    max_history_length:"The chat history will always be truncated to the last N messages.",
  };

  return (
    <>
      <div className="flex join">
        <InputField label="History Mode" help_text = "">
          <select
            className="select join-item w-full"
            name="history_mode"
            onChange={(e) => {
              setHistoryMode(e.target.value);
              props.updateParamValue(e);
            }}
            value={historyMode}
            disabled={props.readOnly}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <small className ="text-muted mt-2">{historyModeHelpTexts[historyMode]}</small>
        </InputField>
      </div>

      {(historyMode === "summarize" || historyMode === "truncate_tokens") && (
        <div className="flex join mb-4">
          <InputField label="Token Limit" help_text = "">
            <input
              className="input join-item w-full"
              name="user_max_token_limit"
              type="number"
              onChange={props.updateParamValue}
              value={userMaxTokenLimit || defaultMaxTokens || ""}
              readOnly={props.readOnly}
            />
            <small className ="text-muted mt-2">Maximum number of tokens before messages are summarized or truncated.</small>
          </InputField>
        </div>
      )}

      {historyMode === "max_history_length" && (
        <div className="flex join mb-4">
          <InputField label="Max History Length" help_text = "">
            <input
              className="input join-item w-full"
              name="max_history_length"
              type="number"
              onChange={props.updateParamValue}
              value={maxHistoryLength || ""}
              readOnly={props.readOnly}
            />
            <small className ="text-muted mt-2">Chat history will only keep the most recent messages up to max history length.</small>
          </InputField>
        </div>
      )}
    </>
  );
}

export function InputField({label, help_text, inputError, children}: React.PropsWithChildren<{
  label: string | ReactNode,
  help_text: string,
  inputError?: string | undefined
}>) {
  return (
    <>
      <div className="fieldset w-full capitalize">
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

function BuiltInToolsWidget(props: WidgetParams) {
  const llmProviderId = concatenate(props.nodeParams["llm_provider_model_id"]);
  const { parameterValues } = getCachedData();
  const models = parameterValues.LlmProviderModelId as LlmProviderModel[];
  const model = models.find((m) => String(m.value) === String(llmProviderId));
  const providerKey = model?.type?.toLowerCase() || "";
  const providerToolMap = parameterValues.built_in_tools as unknown as Record<string, TypedOption[]>
  const options = providerToolMap[providerKey] || [];

  if (options.length === 0) return <></>;

  const toolConfigsMap = parameterValues.built_in_tools_config as unknown as Record<string, Record<string, PropertySchema[]>>;
  const providerToolConfigs = toolConfigsMap[providerKey] || {};

  const toolConfig = props.nodeParams.tool_config || {};
  const [selectedValues, setSelectedValue] = useState(Array.isArray(props.paramValue) ? [...props.paramValue] : []);
  const setNode = usePipelineStore((state) => state.setNode);

  function getNewNodeData(old: Node, updatedList: string[]) {
    return produce(old, (next) => {
      next.data.params[props.name] = updatedList;
    });
  }

  function onUpdate(event: ChangeEvent<HTMLInputElement>) {
    const updatedList = event.target.checked ? [...selectedValues, event.target.name] : selectedValues.filter((tool) => tool !== event.target.name);
    setSelectedValue(updatedList);
    setNode(props.nodeId, (old) => getNewNodeData(old, updatedList));
  }

  function onConfigUpdate(toolName: string, event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) {
    const {name, value} = event.target;
    setNode(props.nodeId, (old) => produce(old, (next) => {
      if (!next.data.params.tool_config) {
        next.data.params.tool_config = {};
      }
      if (!next.data.params.tool_config[toolName]) {
        next.data.params.tool_config[toolName] = {};
      }
      next.data.params.tool_config[toolName][name] = value.split("\n").map(url => {
        const trimmedUrl = url.trim();
        // Strip http:// or https:// prefixes
        return trimmedUrl.replace(/^https?:\/\//, '');
      });
    }))
  }
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      {options.map((option:  { value: string; label: string }) => (
        <div className="flex items-center mb-1" key={option.value}>
          <input
            className="checkbox"
            name={option.value}
            onChange={onUpdate}
            checked={selectedValues.includes(option.value)}
            id={option.value}
            type="checkbox"
            disabled={props.readOnly}
          />
          <span className="ml-2">{option.label}</span>
        </div>
      ))}
      {/* Configs for selected tools */}
      {selectedValues.map((toolKey) => {
        const widgets = providerToolConfigs[toolKey] || [];
        if (!widgets || widgets.length === 0) return null;

        return (
          <div className="mt-3" key={`${toolKey}-config`}>
            <div className="font-medium mb-1 text-sm text-base-content/70">
              {toolKey} configuration
            </div>
            {widgets.map((widget: PropertySchema) => {
              const value = toolConfig[toolKey]?.[widget.name] ?? [];
              const rawError = props.getNodeFieldError(props.nodeId, "tool_config");
              const error = rawError?.includes(`field '${widget.name}'`) ? rawError : "";
              const widgetProps: WidgetParams = {
                ...props,
                name: widget.name,
                label: widget.label,
                helpText: widget.helpText ?? "",
                paramValue: Array.isArray(value) ? value.join("\n") : value,
                updateParamValue: (event) => onConfigUpdate(toolKey, event),
                inputError: error,
              };
              const WidgetComponent = getWidget(widget.type, widget) as React.ComponentType<WidgetParams>;
              return <WidgetComponent key={widget.name} {...widgetProps} />;
            })}
    </div>
    );
    })}
    </InputField>
  );
}

export function TextEditorWidget(props: WidgetParams) {
  const autocomplete_vars_list: string[] = getAutoCompleteList(getSelectOptions(props.schema));
  const modalId = useId();
  const setNode = usePipelineStore((state) => state.setNode);

  const onChangeCallback = (value: string) => {
  setNode(
    props.nodeId,
    produce((draft) => {
      draft.data.params[props.name] = value;
    })
  );
};

  const openModal = () => {
    (document.getElementById(modalId) as HTMLDialogElement)?.showModal();
    }

  const label = (
    <>
      {props.label}
        <div className="tooltip tooltip-left" data-tip={`Expand ${props.label}`}>
        <button
          type="button"
          className="btn btn-xs btn-ghost float-right"
          onClick={openModal}
        >
          <i className="fa-solid fa-expand-alt"></i>
        </button>
      </div>
    </>
  );

  return (
    <>
      <InputField
        label={label}
        help_text={props.helpText}
        inputError={props.inputError}
      >
        <div className="relative w-full">
          <textarea className="textarea textarea-bordered resize-none textarea-sm w-full"
            readOnly={true}
            rows={3}
            value={props.paramValue}
            name={props.name}
          ></textarea>
          <div
            className="absolute inset-0 cursor-pointer"
            onClick={openModal}
          ></div>
        </div>
      </InputField>

      <TextEditorModal
        modalId={modalId}
        value={Array.isArray(props.paramValue) ? props.paramValue.join('') : props.paramValue || ''}
        onChange={onChangeCallback}
        label={props.label}
        inputError={props.inputError}
        autocomplete_vars_list={autocomplete_vars_list}
        readOnly={props.readOnly}
      />
    </>
  );
}

function TextEditorModal({
  modalId,
  value,
  onChange,
  label,
  inputError,
  autocomplete_vars_list,
  readOnly,
}: {
  modalId: string;
  value: string;
  onChange: (val: string) => void;
  label: string;
  inputError?: string;
  autocomplete_vars_list: string[];
  readOnly: boolean;
}) {
  return (
    <dialog id={modalId} className="modal nopan nodelete nodrag noflow nowheel">
      <div className="modal-box min-w-[85vw] h-[80vh] flex flex-col">
        <form method="dialog">
          <button className="btn btn-sm btn-circle btn-ghost absolute right-2 top-2">
            ✕
          </button>
        </form>

        <div className="grow h-full w-full flex flex-col">
          <h4 className="mb-4 font-bold text-lg capitalize">{label}</h4>
          <PromptEditor value={value} onChange={onChange} readOnly={readOnly} autocompleteVars={autocomplete_vars_list}/>;
        </div>

        {inputError && <div className="text-red-500">{inputError}</div>}
      </div>
      <form method="dialog" className="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>
  );
}

function getAutoCompleteList(list: Array<Option>) {
    return Array.isArray(list) ? list.map((v: Option) => v.value) : []
}

export function VoiceWidget(props: WidgetParams) {
  const { parameterValues } = getCachedData();
  const setNode = usePipelineStore((state) => state.setNode);

  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const { value } = event.target;
    const [providerId, syntheticVoiceId] = value.split('|:|');
    setNode(props.nodeId, (old) =>
      produce(old, (next) => {
        next.data.params.voice_provider_id = providerId;
        next.data.params.synthetic_voice_id = syntheticVoiceId;
      })
    );
  };

  const makeValue = (providerId: string, syntheticVoiceId: string) => {
    return providerId + '|:|' + syntheticVoiceId;
  };

  type VoicesByProvider = { [providerId: string]: typeof parameterValues.synthetic_voice_id };
  const voicesByProvider = parameterValues.synthetic_voice_id.reduce((acc, voice) => {
  const voiceProviderId = voice.provider_id || voice.type;
    if (!acc[voiceProviderId]) {
      acc[voiceProviderId] = [];
    }
    acc[voiceProviderId].push(voice);
    return acc;
  }, {} as VoicesByProvider);

  const providerId = concatenate(props.nodeParams.voice_provider_id);
  const syntheticVoiceId = concatenate(props.nodeParams.synthetic_voice_id);
  const value = makeValue(providerId, syntheticVoiceId);

  // Only render if voice is enabled
  if (!parameterValues.voice_enabled) {
    return null;
  }

  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <select
        className="select w-full"
        name={props.name}
        onChange={updateParamValue}
        value={value}
        disabled={props.readOnly}
      >
        <option value="" disabled>
          Select a voice
        </option>

        {parameterValues.voice_provider_id.map((provider) => {
          const providerKey = provider.label.toLowerCase();
          let providerVoices = voicesByProvider[providerKey] || [];

          if (providerVoices.length === 0) {
            providerVoices = voicesByProvider[provider.value] || [];
          }

          if (providerVoices.length === 0) {
            providerVoices = parameterValues.synthetic_voice_id.filter(voice =>
              voice.provider_id === provider.value ||
              voice.provider_id === provider.label ||
              voice.type === provider.label.toLowerCase()
            );
          }
          return providerVoices.map((voice) => (
            <option
              key={provider.value + voice.value}
              value={makeValue(provider.value, voice.value)}
            >
              {voice.label} ({provider.label})
            </option>
          ));
        })}
      </select>
    </InputField>
  );
}