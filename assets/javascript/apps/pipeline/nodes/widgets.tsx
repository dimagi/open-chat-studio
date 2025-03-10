import React, {ChangeEvent, ChangeEventHandler, ReactNode, useEffect, useId, useState,} from "react";
import CodeMirror from '@uiw/react-codemirror';
import {python} from "@codemirror/lang-python";
import {githubDark, githubLight} from "@uiw/codemirror-theme-github";
import {CompletionContext, snippetCompletion as snip} from '@codemirror/autocomplete'
import {TypedOption} from "../types/nodeParameterValues";
import usePipelineStore from "../stores/pipelineStore";
import {classNames, concatenate, getCachedData, getDocumentationLink, getSelectOptions} from "../utils";
import {JsonSchema, NodeParams, PropertySchema} from "../types/nodeParams";
import {Node, useUpdateNodeInternals} from "reactflow";
import DOMPurify from 'dompurify';
import {apiClient} from "../api/api";

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
}

interface ToggleWidgetParams extends Omit<WidgetParams, 'paramValue'> {
  paramValue: boolean;
}


function DefaultWidget(props: WidgetParams) {
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <input
        className="input input-bordered w-full"
        name={props.name}
        onChange={props.updateParamValue}
        value={props.paramValue}
        type="text"
        required={props.required}
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
        className="input input-bordered w-full"
        name={props.name}
        onChange={handleInputChange}
        value={inputValue}
        type="text"
        required={props.required}
      ></input>
    </InputField>
  );
}

function FloatWidget(props: WidgetParams) {
  return <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
    <input
      className="input input-bordered w-full"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="number"
      step=".1"
      required={props.required}
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
      className="input input-bordered w-full input-sm"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="number"
      step=".1"
      required={props.required}
    ></input>
    <input
      className="range range-xs"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      type="range"
      min={getPropOrOther("minimum", "exclusiveMinimum")}
      max={getPropOrOther("maximum", "exclusiveMaximum")}
      step=".1"
      required={props.required}
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
        className="select select-bordered w-full"
        name={props.name}
        onChange={onUpdate}
        value={props.paramValue}
        required={props.required}
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
  let selectedValues = Array.isArray(props.paramValue) ? props.paramValue : [];

  const setNode = usePipelineStore((state) => state.setNode);

  function getNewNodeData(old: Node, updatedList: Array<string>) {
    return {
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          [props.name]: updatedList,
        },
      },
    };
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
          />
          <span className="ml-2">{option.label}</span>
        </div>
      ))}
    </InputField>
  )
}

export function CodeWidget(props: WidgetParams) {
  const [isDarkMode, setIsDarkMode] = useState(false);
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

    useEffect(() => {
        // Set dark / light mode
      setIsDarkMode(document.body.getAttribute("data-theme") === 'dark')
      const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
          if (mutation.type === "attributes") {
            setIsDarkMode(document.body.getAttribute("data-theme") === 'dark')
          }
        });
      });

      observer.observe(document.body, {attributes: true});

    return () => observer.disconnect()
  }, []);

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
            disabled={true}
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
        isDarkMode={isDarkMode}
        inputError={props.inputError}
        documentationLink={getDocumentationLink(props.nodeSchema)}
      />
    </>
  );
}

export function CodeModal(
  { modalId, humanName, value, onChange, isDarkMode, inputError, documentationLink }: {
    modalId: string;
    humanName: string;
    value: string;
    onChange: (value: string) => void;
    isDarkMode: boolean;
    inputError: string | undefined;
    documentationLink: string | null;
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
        <div className="flex-grow h-full w-full flex flex-col">
          <div className="flex justify-between items-center">
            <h4 className="font-bold text-lg bottom-2 capitalize">
              {humanName}
              {documentationLink && <a href={documentationLink} target={"_blank"} className="ml-2 font-light text-info tooltip tooltip-right" data-tip="View Documentation">
                <i className="fa-regular fa-circle-question fa-sm"></i>
              </a>}
            </h4>
            <button className="btn btn-sm btn-ghost" onClick={() => setShowGenerate(!showGenerate)}>
              <i className="fa-solid fa-wand-magic-sparkles"></i>Help
            </button>
          </div>
          <GenerateCodeSection
            showGenerate={showGenerate}
            setShowGenerate={setShowGenerate}
            isDarkMode={isDarkMode}
            onAccept={onChange}
            currentCode={value}
          />
          <CodeNodeEditor
            value={value}
            onChange={onChange}
            isDarkMode={isDarkMode}
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
  isDarkMode,
  onAccept,
  currentCode,
}: {
  showGenerate: boolean;
  setShowGenerate: (value: boolean) => void;
  isDarkMode: boolean;
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
            isDarkMode={isDarkMode}
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

function CodeNodeEditor(
  {value, onChange, isDarkMode}: {
    value: string;
    onChange: (value: string) => void;
    isDarkMode: boolean;
  }
) {
  const customCompletions = {
    get_participant_data: snip("get_participant_data()", {
      label: "get_participant_data",
      type: "keyword",
      detail: "Gets participant data for the current participant",
      boost: 1
    }),
    set_participant_data: snip("set_participant_data(${data})", {
      label: "set_participant_data",
      type: "keyword",
      detail: "Overwrites the participant data with the value provided",
      boost: 1
    }),
    set_temp_state_key: snip("set_temp_state_key(\"${key_name}\", ${data})", {
      label: "set_temp_state_key",
      type: "keyword",
      detail: "Sets the given key in the temporary state. Overwrites the current value",
      boost: 1
    }),
    get_temp_state_key: snip("get_temp_state_key(\"${key_name}\")", {
      label: "get_temp_state_key",
      type: "keyword",
      detail: "Gets the value for the given key from the temporary state",
      boost: 1
    }),
  }

  function pythonCompletions(context: CompletionContext) {
    const word = context.matchBefore(/\w*/)
    if (!word || (word.from == word.to && !context.explicit))
      return null
    return {
      from: word.from,
      options: Object.values(customCompletions).filter(completion =>
        completion.label.toLowerCase().startsWith(word.text.toLowerCase())
      )
    }
  }

  return <CodeMirror
    value={value}
    onChange={onChange}
    className="textarea textarea-bordered h-full w-full flex-grow min-h-48"
    height="100%"
    width="100%"
    theme={isDarkMode ? githubDark : githubLight}
    extensions={[
      python(),
      python().language.data.of({
        autocomplete: pythonCompletions
      })
    ]}
    basicSetup={{
      lineNumbers: true,
      tabSize: 4,
      indentOnInput: true,
    }}
  />
}



export function TextModal(
  {modalId, humanName, name, value, onChange}: {
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
      ></textarea>
      <TextModal
        modalId={modalId}
        humanName={props.label}
        name={props.name}
        value={props.paramValue}
        onChange={props.updateParamValue}>
      </TextModal>
    </InputField>
  );
}

export function KeywordsWidget(props: WidgetParams) {
  const setNode = usePipelineStore((state) => state.setNode);
  const setEdges = usePipelineStore((state) => state.setEdges);
  const updateNodeInternals = useUpdateNodeInternals()

  function getNewNodeData(old: Node, keywords: any[]) {
    return {
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          ["keywords"]: keywords,
        },
      },
    };
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
      return getNewNodeData(old, updatedList);
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
        // update sourceHandle of edges that have a sourceHandle greater than this index to preserve connections
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

  const length = (Array.isArray(props.nodeParams.keywords) ? props.nodeParams.keywords.length : 1);
  const keywords = Array.isArray(props.nodeParams.keywords) ? props.nodeParams["keywords"] : []
  const canDelete = length > 1;
  const defaultMarker = (
    <span className="tooltip normal-case" data-tip="This is the default output if there are no matches">
      <i className="fa-solid fa-asterisk fa-2xs ml-1 text-accent"></i>
    </span>
  )
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
        <small className="text-red-500">{props.inputError}</small>
      </div>
      <div className="ml-2">
        {Array.from({length: length}, (_, index) => {
          const value = keywords ? keywords[index] || "" : "";
          const label = `Output Keyword ${index + 1}`;
          return (
            <div className="form-control w-full capitalize" key={index}>
              <div className="flex justify-between items-center">
                <label className="label">{label}{index === 0 && defaultMarker}</label>
                <div className="tooltip tooltip-left" data-tip={`Delete Keyword ${index + 1}`}>
                  <button className="btn btn-xs btn-ghost" onClick={() => deleteKeyword(index)} disabled={!canDelete}>
                    <i className="fa-solid fa-minus"></i>
                  </button>
                </div>
              </div>
              <input
                className={classNames("input input-bordered w-full", value ? "" : "input-error")}
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

export function LlmWidget(props: WidgetParams) {

  const {parameterValues} = getCachedData();
  const setNode = usePipelineStore((state) => state.setNode);
  const updateParamValue = (event: ChangeEvent<HTMLSelectElement>) => {
    const {value} = event.target;
    const [providerId, providerModelId] = value.split('|:|');
    setNode(props.nodeId, (old) => ({
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

  const providerId = concatenate(props.nodeParams.llm_provider_id);
  const providerModelId = concatenate(props.nodeParams.llm_provider_model_id);
  const value = makeValue(providerId, providerModelId)
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <select
        className="select select-bordered w-full"
        name={props.name}
        onChange={updateParamValue}
        value={value}
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
            className="select select-bordered join-item"
            name={props.name}
            onChange={props.updateParamValue}
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
          <InputField label="History Name" help_text={props.helpText}>
            <input
              className="input input-bordered join-item"
              name="history_name"
              onChange={props.updateParamValue}
              value={historyName || ""}
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
  const [historyMode, setHistoryMode] = useState(initialHistoryMode || "Summarize");

  const historyModeHelpTexts: Record<string, string> = {
    summarize:"If the token count exceeds the limit, older messages will be summarized while keeping the last few messages intact.",
    truncate_tokens:"If the token count exceeds the limit, older messages will be removed until the token count is below the limit.",
    max_history_length:"The chat history will always be truncated to the last N messages.",
  };

  return (
    <>
      <div className="flex join">
        <InputField label="History Mode">
          <select
            className="select select-bordered join-item"
            name="history_mode"
            onChange={(e) => {
              setHistoryMode(e.target.value);
              props.updateParamValue(e);
            }}
            value={historyMode}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <br />
          <br />
          <div>{historyModeHelpTexts[historyMode]}</div>
        </InputField>
      </div>

      {(historyMode === "summarize" || historyMode === "truncate_tokens") && (
        <div className="flex join mb-4">
          <InputField label="Token Limit">
            <input
              className="input input-bordered join-item"
              name="user_max_token_limit"
              type="number"
              onChange={props.updateParamValue}
              value={userMaxTokenLimit || ""}
            />
            <br />
            <div>Maximum number of tokens before messages are summarized or truncated.</div>
          </InputField>
        </div>
      )}

      {historyMode === "max_history_length" && (
        <div className="flex join mb-4">
          <InputField label="Max History Length">
            <input
              className="input input-bordered join-item"
              name="max_history_length"
              type="number"
              onChange={props.updateParamValue}
              value={maxHistoryLength || ""}
            />
            <br />
            <div>Chat history will only keep the most recent messages up to max history length.</div>
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
