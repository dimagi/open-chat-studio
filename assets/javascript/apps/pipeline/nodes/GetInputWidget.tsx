import React from "react";
import {NodeParams, PropertySchema} from "../types/nodeParams";
import usePipelineManagerStore from "../stores/pipelineManagerStore";
import {getWidget} from "./widgets";


type InputWidgetParams = {
  id: string;
  name: string;
  schema: PropertySchema;
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
  "AssistantNode": ["assistant_id", "citations_enabled"],
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
  const widgetOrType = params.schema["ui:widget"] || params.schema.type;
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
      label={params.schema.title || params.name.replace(/_/g, " ")}
      helpText={params.schema.description || ""}
      paramValue={paramValue || ""}
      inputError={fieldError}
      updateParamValue={params.updateParamValue}
      schema={params.schema}
      nodeParams={params.params}
      required={params.required}
      getFieldError={getFieldError}
    />
  )
};
