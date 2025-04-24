import {Node, NodeProps, NodeToolbar, Position} from "reactflow";
import React, {ChangeEvent} from "react";
import {concatenate, formatDocsForSchema, getCachedData, nodeBorderClass} from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import useEditorStore from "./stores/editorStore";
import {JsonSchema, NodeData} from "./types/nodeParams";
import {getWidgetsForNode} from "./nodes/GetInputWidget";
import NodeInput from "./nodes/NodeInput";
import NodeOutputs from "./nodes/NodeOutputs";
import {HelpContent} from "./panel/ComponentHelp";
import { produce } from "immer";

export type PipelineNode = Node<NodeData>;

export function PipelineNode(nodeProps: NodeProps<NodeData>) {
  const {id, data, selected} = nodeProps;
  const openEditorForNode = useEditorStore((state) => state.openEditorForNode)
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const hasErrors = usePipelineStore((state) => state.nodeHasErrors(id));
  const nodeError = usePipelineStore((state) => state.getNodeFieldError(id, "root"));
  const nodeSchema = getCachedData().nodeSchemas.get(data.type)!;

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const {name, value} = event.target;
    let updateValue: string | boolean = value
    if (event.target instanceof HTMLInputElement && event.target.type === "checkbox") {
      updateValue = event.target.checked;
    }

    setNode(id, produce((next) => {
      next.data.params[name] = updateValue;
    }));
  };

  const editNode = () => {
    openEditorForNode(nodeProps);
  }

  const nodeDocs = formatDocsForSchema(nodeSchema);

  return (
    <>
      <NodeToolbar position={Position.Top} isVisible={hasErrors || selected}>
        <div className="join">
            <button
              className="btn btn-xs join-item"
              onClick={() => deleteNode(id)}>
                <i className="fa fa-trash-o"></i>
            </button>
            {Object.keys(nodeSchema.properties).length > 0 && (
              <button className="btn btn-xs join-item" onClick={() => editNode()}>
                  <i className="fa fa-pencil"></i>
              </button>
            )}
            {nodeDocs && (
              <div className="dropdown dropdown-right">
                  <button className="btn btn-xs join-item">
                      <i className={"fa-regular fa-circle-question"}></i>
                  </button>
                  <HelpContent>{nodeDocs}</HelpContent>
              </div>
            )}
            {nodeError && (
                <div className="dropdown dropdown-top">
                    <button className="btn btn-xs join-item">
                        <i className="fa-solid fa-exclamation-triangle text-warning"></i>
                    </button>
                    <HelpContent><p>{nodeError}</p></HelpContent>
                </div>
              )}
        </div>
      </NodeToolbar>
      <div className={nodeBorderClass(hasErrors, selected)}>
        <NodeHeader nodeSchema={nodeSchema} nodeName={concatenate(data.params["name"])} />

        <NodeInput />
        <div className="px-4">
          <div>
            {getWidgetsForNode({schema: nodeSchema, nodeId: id, nodeData: data, updateParamValue: updateParamValue})}
          </div>
          <div className="mt-2">
            <button className="btn btn-sm btn-ghost w-full"
                    onClick={() => editNode()}>
              Advanced
            </button>
          </div>
        </div>
        <NodeOutputs data={data} />
      </div>
    </>
  );
}

function NodeHeader({nodeSchema, nodeName}: {nodeSchema: JsonSchema, nodeName: string}) {
  const defaultNodeNameRegex = /^[A-Za-z]+-[a-zA-Z0-9]{5}$/;
  const hasCustomName = !defaultNodeNameRegex.test(nodeName);
  const header = hasCustomName ? nodeName : nodeSchema["ui:label"];
  const subheader = hasCustomName ? nodeSchema["ui:label"] : "";
  return (
    <div className="m-1 text-lg font-bold text-center">
      <DeprecationNotice nodeSchema={nodeSchema} />
      {header}
      {subheader && <div className="text-sm">{subheader}</div>}
    </div>
  );
}


function DeprecationNotice({nodeSchema}: {nodeSchema: JsonSchema}) {
  if (!nodeSchema["ui:deprecated"]) {
    return <></>;
  }
  const customMessage = nodeSchema["ui:deprecation_message"] || "";
  return (
    <div className="mr-2 text-warning inline-block tooltip"
         data-tip={`This node type has been deprecated and will be removed in future. ${customMessage}`}>
      <i className="fa-solid fa-exclamation-triangle"></i>
    </div>
  )
}
