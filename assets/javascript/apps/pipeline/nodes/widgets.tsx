import React, {ChangeEvent, ChangeEventHandler, ReactNode, useEffect, useId, useState,} from "react";
import CodeMirror, {EditorState} from '@uiw/react-codemirror';
import {python} from "@codemirror/lang-python";
import {githubDarkInit, githubLightInit} from "@uiw/codemirror-theme-github";
import {CompletionContext, snippetCompletion as snip, autocompletion, Completion} from '@codemirror/autocomplete'
import {TypedOption, LlmProviderModel, Option} from "../types/nodeParameterValues";
import usePipelineStore from "../stores/pipelineStore";
import {classNames, concatenate, getCachedData, getDocumentationLink, getSelectOptions} from "../utils";
import {JsonSchema, NodeParams, PropertySchema} from "../types/nodeParams";
import {Node, useUpdateNodeInternals} from "reactflow";
import DOMPurify from 'dompurify';
import {apiClient} from "../api/api";
import { produce } from "immer";
import { EditorView,ViewPlugin, Decoration, ViewUpdate, DecorationSet } from '@codemirror/view';


const githubDark = githubDarkInit({
  "settings": {
    background: "oklch(22% 0.016 252.42)",
    foreground: "oklch(97% 0.029 256.847)",
    fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
  }
})

const githubLight = githubLightInit({
  "settings": {
    fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
  }
})

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
      setIsDarkMode(document.documentElement.getAttribute("data-theme") === 'dark')
      const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
          if (mutation.type === "attributes") {
            setIsDarkMode(document.documentElement.getAttribute("data-theme") === 'dark')
          }
        });
      });

      observer.observe(document.documentElement, {attributes: true});

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
        isDarkMode={isDarkMode}
        inputError={props.inputError}
        documentationLink={getDocumentationLink(props.nodeSchema)}
        readOnly={props.readOnly}
      />
    </>
  );
}

export function CodeModal(
  { modalId, humanName, value, onChange, isDarkMode, inputError, documentationLink, readOnly }: {
    modalId: string;
    humanName: string;
    value: string;
    onChange: (value: string) => void;
    isDarkMode: boolean;
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
            isDarkMode={isDarkMode}
            onAccept={onChange}
            currentCode={value}
          />}
          <CodeNodeEditor
            value={value}
            onChange={onChange}
            isDarkMode={isDarkMode}
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

function CodeNodeEditor(
  {value, onChange, isDarkMode, readOnly}: {
    value: string;
    onChange: (value: string) => void;
    isDarkMode: boolean;
    readOnly: boolean;
  }
) {
  const customCompletions = {
    get_participant_data: snip("get_participant_data()", {
      label: "get_participant_data",
      type: "function",
      detail: "Gets participant data for the current participant",
      boost: 1,
      section: "Participant Data"
    }),
    set_participant_data: snip("set_participant_data(${data})", {
      label: "set_participant_data",
      type: "function",
      detail: "Overwrites the participant data with the value provided",
      boost: 1,
      section: "Participant Data",
    }),
    set_temp_state_key: snip("set_temp_state_key(\"${key_name}\", ${data})", {
      label: "set_temp_state_key",
      type: "function",
      detail: "Sets the given key in the temporary state. Overwrites the current value",
      boost: 1,
      section: "Temporary Data",
    }),
    get_temp_state_key: snip("get_temp_state_key(\"${key_name}\")", {
      label: "get_temp_state_key",
      type: "function",
      detail: "Gets the value for the given key from the temporary state",
      boost: 1,
      section: "Temporary Data",
    }),
    get_session_state: snip("get_session_state_key(\"${key_name}\")", {
      label: "get_session_state_key",
      type: "function",
      detail: "Gets the value for the given key from the session's state",
      boost: 1,
      section: "Session Data",
    }),
    set_session_state: snip("set_session_state_key(\"${key_name}\", ${data})", {
      label: "set_session_state_key",
      type: "function",
      detail: "Sets the given key in the session's state. Overwrites the current value",
      boost: 1,
      section: "Session Data",
    }),
    get_selected_route: snip("get_selected_route(\"${router_node_name}\")", {
      label: "get_selected_route",
      type: "function",
      detail: "Gets the route selected by a specific router node",
      boost: 1,
      section: "Routing"
    }),
    get_node_path: snip("get_node_path(\"${node_name}\")", {
      label: "get_node_path",
      type: "function",
      detail: "Gets the path (list of node names) leading to the specified node",
      boost: 1,
      section: "Routing"
    }),
    get_all_routes: snip("get_all_routes()", {
      label: "get_all_routes",
      type: "function",
      detail: "Gets all routing decisions in the pipeline",
      boost: 1,
      section: "Routing"
    }),
    get_node_output: snip("get_node_output(\"${node_name}\")", {
      label: "get_node_output",
      type: "function",
      detail: "Returns the output of the specified node if it has been executed. If the node has not been executed, it returns `None`.",
      boost: 1,
      section: "Node Outputs"
    }),

    add_message_tag: snip("add_message_tag(\"${tag_name}\")", {
      label: "add_message_tag",
      type: "function",
      detail: "Adds the tag to the output message",
      boost: 1
    }),

    add_session_tag: snip("add_session_tag(\"${tag_name}\")", {
      label: "add_session_tag",
      type: "function",
      detail: "Adds the tag to the chat session",
      boost: 1
    }),
    abort_with_message: snip("abort_with_message(\"${message}\", tag_name=\"${tag_name}\")", {
      label: "abort_with_message",
      type: "function",
      detail: "Terminates the pipeline execution. No further nodes will get executed in any branch of the pipeline graph.",
      boost: 1,
      section: "Flow Control",
    }),
    require_node_outputs: snip("require_node_outputs(${node_names})", {
      label: "require_node_outputs",
      type: "function",
      detail: "Ensures that the specified nodes have been executed and their outputs are available in the pipeline's state.",
      boost: 1,
      section: "Flow Control",
    })
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

  let extensions = [
    python(),
    python().language.data.of({
      autocomplete: pythonCompletions
    })
  ];
  if (readOnly) {
    extensions = [
      ...extensions,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ]
  }
  return <CodeMirror
    value={value}
    onChange={onChange}
    className="textarea textarea-bordered h-full w-full grow min-h-48"
    height="100%"
    width="100%"
    theme={isDarkMode ? githubDark : githubLight}
    extensions={extensions}
    basicSetup={{
      lineNumbers: true,
      tabSize: 4,
      indentOnInput: true,
    }}
  />
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
  const { parameterValues } = getCachedData();
  const autocomplete_vars_list: string[] = Array.isArray(parameterValues.text_editor_autocomplete_vars)
  ? parameterValues.text_editor_autocomplete_vars.map((v: Option) => v.value) : [];

  const modalId = useId();
  const [isDarkMode, setIsDarkMode] = useState(false);
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

  useEffect(() => {
    const updateTheme = () =>
      setIsDarkMode(document.documentElement.getAttribute("data-theme") === "dark");
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(document.documentElement, { attributes: true });
    return () => observer.disconnect();
  }, []);

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
        isDarkMode={isDarkMode}
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
  isDarkMode,
  label,
  inputError,
  autocomplete_vars_list,
  readOnly,
}: {
  modalId: string;
  value: string;
  onChange: (val: string) => void;
  isDarkMode: boolean;
  label: string;
  inputError?: string;
  autocomplete_vars_list: string[];
  readOnly: boolean;
}) {
  let extensions = [
    autocompletion({
      override: [textEditorVarCompletions(autocomplete_vars_list)],
      activateOnTyping: true,
    }),
    highlightAutoCompleteVars(autocomplete_vars_list),
    autocompleteVarTheme(isDarkMode),
    EditorView.lineWrapping,
  ];
  if (readOnly) {
    extensions = [
      ...extensions,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ]
  }
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

          <CodeMirror
            value={value}
            onChange={onChange}
            height="100%"
            theme={isDarkMode ? githubDark : githubLight}
            extensions={extensions}
            basicSetup={{
              lineNumbers: true,
              tabSize: 2,
              indentOnInput: true,
            }}
          />
        </div>

        {inputError && <div className="text-red-500">{inputError}</div>}
      </div>
      <form method="dialog" className="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>
  );
}

function textEditorVarCompletions(autocomplete_vars_list: string[]) {
  return (context: CompletionContext) => {
    const word = context.matchBefore(/[a-zA-Z0-9._[\]"]*$/);
    if (!word || (word.from === word.to && !context.explicit)) return null;

    return {
      from: word.from,
      options: autocomplete_vars_list.map((v) => ({
        label: v,
        type: "variable",
        info: `Insert {${v}}`,
        apply: (view: EditorView, completion: Completion, from: number, to: number) => {
          const beforeText = view.state.doc.sliceString(from - 1, from);
          const insertText =
            beforeText === "{" ? `${completion.label}` : `{${completion.label}}`;
          view.dispatch({
            changes: { from, to, insert: insertText },
          });
        },
      })),
    };
  };
}
// highlight auto complete words. valid - blue, invalid - red
function highlightAutoCompleteVars(autocomplete_vars_list: string[]) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.buildDecorations(view);
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this.buildDecorations(update.view);
        }
      }

      buildDecorations(view: EditorView) {
        const widgets: any[] = [];
        const text = view.state.doc.toString();
        const regex = /\{([a-zA-Z0-9._[\]"]+)\}/g;
        let match;
        while ((match = regex.exec(text)) !== null) {
          const varName = match[1];
          const from = match.index;
          const to = from + match[0].length;
          const isValidVar = autocomplete_vars_list.some(
            v => varName === v || varName.startsWith(v + ".") || varName.startsWith(v + "[")
        );
          const deco = Decoration.mark({
            class: isValidVar
              ? "autocomplete-var-valid"
              : "autocomplete-var-invalid",
          });
          widgets.push(deco.range(from, to));
        }
        return Decoration.set(widgets);
      }
    },
    {
      decorations: (v) => v.decorations,
    }
  );
}

const autocompleteVarTheme = (isDarkMode: boolean) =>
  EditorView.theme({
    ".autocomplete-var-valid": {
      color: isDarkMode ? "#93c5fd" : "navy",
      fontWeight: "bold",
    },
    ".autocomplete-var-invalid": {
      color: isDarkMode ? "#f87171" : "red",
      fontWeight: "bold",
    },
  });
