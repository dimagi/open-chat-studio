import React from "react";
import {JsonSchema, NodeParams} from "../types/nodeParams";
import usePipelineStore from "../stores/pipelineStore";
import {getWidget} from "./widgets";
import {getCachedData} from "../utils";


type GetWidgetsParams = {
  schema: JsonSchema;
  nodeId: string;
  nodeData: any;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
}

type GetWidgetParamsGeneric = GetWidgetsParams & {
  widgetGenerator: (params: InputWidgetParams) => React.ReactElement<any>;
}


type InputWidgetParams = {
  id: string;
  name: string;
  schema: JsonSchema;
  params: NodeParams;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
  nodeType: string;
  required: boolean;
}

const nodeTypeToInputParamsMap: Record<string, string[]> = {
  "RouterNode": ["llm_model", "history_type", "prompt"],
  "StaticRouterNode": ["data_source", "route_key"],
  "ExtractParticipantData": ["llm_model", "history_type", "data_schema"],
  "ExtractStructuredData": ["llm_model", "history_type", "data_schema"],
  "LLMResponseWithPrompt": ["llm_model", "history_type", "prompt"],
  "LLMResponse": ["llm_model", "history_type"],
  "AssistantNode": ["assistant_id", "citations_enabled"],
};

/**
 * Retrieves the full list of widgets for the given schema
 */
export const getWidgets = (
  {schema, nodeId, nodeData, updateParamValue}: GetWidgetsParams
) => {
  return getWidgetsGeneric({schema, nodeId, nodeData, updateParamValue, widgetGenerator: getInputWidget});
}

/**
 * Retrieves the list of widgets for the given schema which should be displayed on a node
 */
export const getWidgetsForNode = (
  {schema, nodeId, nodeData, updateParamValue}: GetWidgetsParams
) => {
  return getWidgetsGeneric({schema, nodeId, nodeData, updateParamValue, widgetGenerator: getNodeInputWidget});
}

const getWidgetsGeneric = (
  {schema, nodeId, nodeData, updateParamValue, widgetGenerator}: GetWidgetParamsGeneric
) => {
  const schemaProperties = Object.getOwnPropertyNames(schema.properties);
  const requiredProperties = schema.required || [];
  schemaProperties.sort((a, b) => {
    // 'name' should always be first
    if (a === "name") return -1;
    if (b === "name") return 1;

    if (schema["ui:order"]) {
      const indexA = schema["ui:order"]!.indexOf(a);
      const indexB = schema["ui:order"]!.indexOf(b);
      // If 'a' is not in the order list, it should be at the end
      if (indexA === -1) return 1;
      if (indexB === -1) return -1;
      return indexA - indexB;
    } else {
      return 0;
    }
  });
   if (!Array.isArray(nodeData.params.keywords)) {
    nodeData.params.keywords = [""]; // initialize keywords with size 1
  }
  return schemaProperties.map((name) => (
    <React.Fragment key={name}>
      {widgetGenerator({
        id: nodeId,
        name: name,
        schema: schema,
        params: nodeData.params,
        updateParamValue: updateParamValue,
        nodeType: nodeData.type,
        required: requiredProperties.includes(name),
      })}
    </React.Fragment>
  ));
}

/**
 * Retrieves the appropriate input widget for the specified node type and parameter.
 *
 * This calls `getInputWidget` under the hood but also filters the parameters to only those which
 * should be shown on the node.
 *
 * @returns The input widget for the specified node type and parameter.
 */
export const getNodeInputWidget = (param: InputWidgetParams) => {
  if (!param.nodeType) {
    return <></>;
  }

  const allowedInNode = nodeTypeToInputParamsMap[param.nodeType];
  if (param.name == "name" || (allowedInNode && !allowedInNode.includes(param.name))) {
    /* name param is always in the advanced box */
    return <></>;
  }
  return getInputWidget(param);
}

/**
 * Generates the appropriate input widget based on the input parameter type.
 * @returns The input widget for the specified parameter type.
 */
export const getInputWidget = (params: InputWidgetParams) => {
  if (params.name == "llm_model" || params.name == "max_token_limit") {
    /*
       This is here as we migrated llm_model to llm_provider_model_id, in October 2024.
       During the migration, we kept the data in llm_model as a safeguard. This check can safely be deleted once a second migration to delete all instances of llm_model has been run.
       TODO: Remove this check once there are no instances of llm_model or max_token_limit in the node definitions.
     */
    return <></>
  }

  const {flagsEnabled} = getCachedData();
  const requiredFlag = params.schema.properties[params.name]["ui:flagRequired"]
  if (requiredFlag && !flagsEnabled.includes(requiredFlag)) {
    return <></>
  }

  const widgetSchema = params.schema.properties[params.name];
  const widgetOrType = widgetSchema["ui:widget"] || widgetSchema.type;
  if (widgetOrType == 'none') {
    return <></>;
  }

  const getNodeFieldError = usePipelineStore((state) => state.getNodeFieldError);
  const readOnly = usePipelineStore((state) => state.readOnly);
  const Widget = getWidget(widgetOrType, widgetSchema)
  let fieldError = getNodeFieldError(params.id, params.name);
  const paramValue = params.params[params.name];
  if (params.required && (paramValue === null || paramValue === undefined)) {
    fieldError = "This field is required";
  }
  return (
    <Widget
      nodeId={params.id}
      name={params.name}
      label={widgetSchema.title || params.name.replace(/_/g, " ")}
      helpText={widgetSchema.description || ""}
      paramValue={paramValue ?? ""}
      inputError={fieldError}
      updateParamValue={params.updateParamValue}
      schema={widgetSchema}
      nodeParams={params.params}
      nodeSchema={params.schema}
      required={params.required}
      getNodeFieldError={getNodeFieldError}
      readOnly={readOnly}
    />
  );
};
