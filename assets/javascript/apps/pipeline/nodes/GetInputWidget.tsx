import {
  HistoryTypeWidget,
  ExpandableTextWidget,
  InputField, LlmWidget, KeywordsWidget
} from "../widgets";
import React from "react";
import {getCachedData, concatenate} from "../utils";
import {InputSchema, NodeParams} from "../types/nodeParams";
import usePipelineManagerStore from "../stores/pipelineManagerStore";
import {Option} from "../types/nodeParameterValues";


type InputWidgetParams = {
  id: string;
  name: string;
  inputParam: InputSchema;
  params: NodeParams;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
  nodeType: string;
  required: boolean;
}

const nodeTypeToInputParamsMap: Record<string, string[]> = {
  "RouterNode": ["llm_model", "history_type", "prompt"],
  "ExtractParticipantData": ["llm_model", "history_type", "data_schema"],
  "ExtractStructuredData": ["llm_model", "history_type", "data_schema"],
  "LLMResponseWithPrompt": ["llm_model", "history_type", "prompt"],
  "LLMResponse": ["llm_model", "history_type"],
};

export const showAdvancedButton = (nodeType: string) => {
  return nodeTypeToInputParamsMap[nodeType] !== undefined;
}

export const getNodeInputWidget = (param: InputWidgetParams) => {
  if (!param.nodeType) {
    return <></>;
  }

  const allowedInNode = nodeTypeToInputParamsMap[param.nodeType];
  if (allowedInNode && !allowedInNode.includes(param.name)) {
    return <></>;
  }
  return getInputWidget(param);
}

/**
 * Generates the appropriate input widget based on the input parameter type.
 * @param id - The node ID
 * @param inputParam - The input parameter to generate the widget for.
 * @param params - The parameters for the node.
 * @param setParams - The function to update the node parameters.
 * @param updateParamValue - The function to update the value of the input parameter.
 * @returns The input widget for the specified parameter type.
 */
export const getInputWidget = (params: InputWidgetParams) => {
  if (params.name == "llm_model" || params.name == "max_token_limit") {
    /*
       This is here as we migrated llm_model to llm_provider_model_id, in October 2024.
       During the migration, we kept the data in llm_model as a safeguard. This check can safely be deleted once a second migration to delete all instances of llm_model has been run.
       TODO: Remove this check once there are no instances of llm_model or max_token_limit in the node definitions.
     */
    return
  }

  const getFieldError = usePipelineManagerStore((state) => state.getFieldError);
  const widgetOrType = params.inputParam["ui:widget"] || params.inputParam.type;
  if (widgetOrType == 'none') {
    return <></>;
  }

  const Widget = getWidget(widgetOrType)
  let fieldError = getFieldError(params.id, params.name);
  const paramValue = params.params[params.name];
  if (params.required && (paramValue === null || paramValue === undefined)) {
    fieldError = "This field is required";
  }
  return (
    <Widget
      nodeId={params.id}
      name={params.name}
      label={params.inputParam.title || params.name.replace(/_/g, " ")}
      helpText={params.inputParam.description || ""}
      paramValue={paramValue || ""}
      inputError={fieldError}
      updateParamValue={params.updateParamValue}
      inputSchema={params.inputParam}
      nodeParams={params.params}
      required={params.required}
    />
  )
};

function getWidget(name: string) {
  switch (name) {
    case "toggle":
      return ToggleWidget
    case "float":
      return FloatFactory
    case "expandable_text":
      return ExandableTextWidget
    case "select":
      return SelectWidget
    case "llm_provider_model":
      return LlmProviderWidget
    case "history":
      return HistoryTypeWidgetFactory
    case "keywords":
      return KeywordsWidgetFactory
    case "email_list":
    default:
      return DefaultFactory
  }
}

interface WidgetFactoryParams {
  nodeId: string;
  name: string;
  label: string;
  helpText: string;
  paramValue: string | string[];
  inputError: string | undefined;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
  inputSchema: InputSchema
  nodeParams: NodeParams
  required: boolean
}

function DefaultFactory(props: WidgetFactoryParams) {
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

function FloatFactory(props: WidgetFactoryParams) {
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

function ToggleWidget(props: WidgetFactoryParams) {
  const onChangeCallback = (event: React.ChangeEvent<HTMLInputElement>) => {
    event.target.value = event.target.checked ? "true" : "false";
    props.updateParamValue(event);
  };
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <input
        className="toggle"
        name={props.name}
        onChange={onChangeCallback}
        checked={props.paramValue === "true"}
        type="checkbox"
      ></input>
    </InputField>
  );
}

function SelectWidget(props: WidgetFactoryParams) {
  const {parameterValues} = getCachedData();
  let options: Option[] = [];
  if (props.inputSchema["ui:optionsSource"]) {
    options = parameterValues[props.inputSchema["ui:optionsSource"]];
  }
  return <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
    <select
      className="select select-bordered w-full"
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      required={props.required}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  </InputField>
}

function LlmProviderWidget(props: WidgetFactoryParams) {
  return (
    <InputField label={props.label} help_text={props.helpText} inputError={props.inputError}>
      <LlmWidget
        name={props.name}
        nodeId={props.nodeId}
        providerId={concatenate(props.nodeParams.llm_provider_id)}
        providerModelId={concatenate(props.nodeParams.llm_provider_model_id)}
      ></LlmWidget>
    </InputField>
  );
}

function ExandableTextWidget(props: WidgetFactoryParams) {
  return (
    <ExpandableTextWidget
      humanName={props.label}
      name={props.name}
      onChange={props.updateParamValue}
      value={props.paramValue}
      help_text={props.helpText}
      inputError={props.inputError}>
    </ExpandableTextWidget>
  );
}

function KeywordsWidgetFactory(props: WidgetFactoryParams) {
  return <KeywordsWidget nodeId={props.nodeId} params={props.nodeParams} inputError={props.inputError}/>
}


function HistoryTypeWidgetFactory(props: WidgetFactoryParams) {
  return (
    <HistoryTypeWidget
      onChange={props.updateParamValue}
      name={props.name}
      historyType={concatenate(props.paramValue)}
      historyName={concatenate(props.nodeParams["history_name"])}
      help_text={props.helpText}
    ></HistoryTypeWidget>
  );
}
