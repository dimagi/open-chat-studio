import React, {useEffect, useRef} from "react";
import {JsonSchema, NodeParams, VisibleWhenCondition} from "../types/nodeParams";
import usePipelineStore from "../stores/pipelineStore";
import {getWidget} from "./widgets";
import {getCachedData} from "../utils";

/**
 * Evaluates a single visibility condition against the current node params.
 * Returns true if the field should be visible.
 */
function evaluateCondition(condition: VisibleWhenCondition, nodeParams: NodeParams): boolean {
  const fieldValue = nodeParams[condition.field];
  const operator = condition.operator ?? "==";
  switch (operator) {
    case "==": return fieldValue === condition.value;
    case "!=": return fieldValue !== condition.value;
    case "in": return Array.isArray(condition.value) && condition.value.includes(fieldValue);
    case "not_in": return Array.isArray(condition.value) && !condition.value.includes(fieldValue);
    case "is_empty": return !fieldValue || (Array.isArray(fieldValue) && fieldValue.length === 0);
    case "is_not_empty": return !!fieldValue && (!Array.isArray(fieldValue) || fieldValue.length > 0);
    default: return true;
  }
}

/**
 * Evaluates the ui:visibleWhen condition(s) for a field.
 * Returns true if the field should be visible, false if it should be hidden.
 * If no condition is defined, returns true (field is always visible).
 */
export function evaluateVisibleWhen(
  visibleWhen: VisibleWhenCondition | VisibleWhenCondition[] | undefined,
  nodeParams: NodeParams
): boolean {
  if (visibleWhen === undefined || visibleWhen === null) {
    return true;
  }
  if (Array.isArray(visibleWhen)) {
    return visibleWhen.every((condition) => evaluateCondition(condition, nodeParams));
  }
  return evaluateCondition(visibleWhen, nodeParams);
}


type VisibleWhenWrapperProps = {
  visibleWhen: VisibleWhenCondition | VisibleWhenCondition[] | undefined;
  nodeParams: NodeParams;
  fieldName: string;
  nodeId: string;
  schemaDefault: any;
  onHide?: () => void;
  children: React.ReactNode;
}

/**
 * Wraps a field widget with visibility logic. When the field transitions from
 * visible to hidden, its value is cleared to prevent stale values from causing
 * backend validation errors.
 *
 * The optional `onHide` callback overrides the default reset behaviour, which
 * is useful for sub-schema widgets (e.g. ModelParametersWidget) that store
 * their values at a nested path rather than directly in node.data.params.
 */
const VisibleWhenWrapper: React.FC<VisibleWhenWrapperProps> = ({
  visibleWhen,
  nodeParams,
  fieldName,
  nodeId,
  schemaDefault,
  onHide,
  children,
}) => {
  const setNode = usePipelineStore((state) => state.setNode);
  const isVisible = evaluateVisibleWhen(visibleWhen, nodeParams);
  const prevVisibleRef = useRef(isVisible);

  useEffect(() => {
    if (prevVisibleRef.current && !isVisible) {
      if (onHide) {
        onHide();
      } else {
        // set the value to the default value or 'null' when it isn't visible
        setNode(nodeId, (oldNode) => ({
          ...oldNode,
          data: {
            ...oldNode.data,
            params: {
              ...oldNode.data.params,
              [fieldName]: schemaDefault ?? null,
            },
          },
        }));
      }
    }
    prevVisibleRef.current = isVisible;
  }, [isVisible]);

  if (!isVisible) return <></>;
  return <>{children}</>;
};

type GetWidgetsParams = {
  schema: JsonSchema;
  nodeId: string;
  nodeData: any;
  updateParamValue: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>) => any;
}

type GetWidgetParamsGeneric = GetWidgetsParams & {
  widgetGenerator: (
    params: InputWidgetParams
  ) => React.ReactElement<any>;
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
  const getNodeFieldError = usePipelineStore((state) => state.getNodeFieldError);
  const readOnly = usePipelineStore((state) => state.readOnly);

  const wrappedInputWidget = (params: InputWidgetParams) => getInputWidget(params, getNodeFieldError, readOnly);
  return getWidgetsGeneric({schema, nodeId, nodeData, updateParamValue, widgetGenerator: wrappedInputWidget});
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

  const getNodeFieldError = usePipelineStore((state) => state.getNodeFieldError);
  const readOnly = usePipelineStore((state) => state.readOnly);

  return getInputWidget(param, getNodeFieldError, readOnly);
}

/**
 * Generates the appropriate input widget based on the input parameter type.
 *
 * The optional `onHide` callback is forwarded to `VisibleWhenWrapper` and
 * overrides the default field-reset behaviour when a field becomes hidden.
 * Callers that render widgets for a sub-schema (e.g. ModelParametersWidget)
 * should supply an `onHide` that writes to the correct nested path.
 *
 * @returns The input widget for the specified parameter type.
 */
export const getInputWidget = (
  params: InputWidgetParams,
  getNodeFieldError: (nodeId: string, fieldName: string) => string | undefined,
  readOnly: boolean,
  onHide?: () => void,
) => {
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

  const Widget = getWidget(widgetOrType, widgetSchema)
  let fieldError = getNodeFieldError(params.id, params.name);
  let paramValue = params.params[params.name];

  // Use schema default if paramValue is undefined
  if (paramValue === undefined && widgetSchema.default !== undefined) {
    paramValue = widgetSchema.default;
  }

  if (params.required && (paramValue === null || paramValue === undefined)) {
    fieldError = "This field is required";
  }
  return (
    <VisibleWhenWrapper
      visibleWhen={widgetSchema["ui:visibleWhen"]}
      nodeParams={params.params}
      fieldName={params.name}
      nodeId={params.id}
      schemaDefault={widgetSchema.default}
      onHide={onHide}
    >
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
    </VisibleWhenWrapper>
  );
};
