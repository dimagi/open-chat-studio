import {Node, NodeProps, NodeToolbar, Position} from "reactflow";
import React, {ChangeEvent, MouseEvent} from "react";
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

// Predefined node colors with labels
const NODE_COLORS = [
  { value: "bg-base-100", label: "Default"},
  { value: "bg-red-100 dark:bg-red-950", label: "Red"},
  { value: "bg-yellow-100 dark:bg-yellow-950", label: "Yellow"},
  { value: "bg-green-100 dark:bg-green-950", label: "Green"},
  { value: "bg-blue-100 dark:bg-blue-950", label: "Blue"},
  { value: "bg-purple-100 dark:bg-purple-950", label: "Purple"},
  { value: "bg-pink-100 dark:bg-pink-950", label: "Pink"},
  { value: "bg-indigo-100 dark:bg-indigo-950", label: "Indigo"},
];

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

  const changeNodeColor = (color: string | undefined) => {
    setNode(id, produce((next) => {
      next.data.params["color"] = color;
    }));
  };

  const currentColor = data.params["color"] || NODE_COLORS[0].value;
  const nodeClasses = `${nodeBorderClass(hasErrors, selected)} ${currentColor}`;

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
      <div className={nodeClasses}>
        <NodeHeader
          nodeId={id}
          nodeSchema={nodeSchema}
          nodeName={concatenate(data.params["name"])}
          onIconClick={changeNodeColor}
          currentColor={currentColor}
        />

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

function NodeHeader({
  nodeId,
  nodeSchema,
  nodeName,
  onIconClick,
  currentColor
}: {
  nodeId: string,
  nodeSchema: JsonSchema,
  nodeName: string,
  onIconClick?: (color: string | undefined) => void,
  currentColor?: string
}) {
  const defaultNodeNameRegex = /^[A-Za-z]+-[a-zA-Z0-9]{5}$/;
  const hasCustomName = !defaultNodeNameRegex.test(nodeName);
  const header = hasCustomName ? nodeName : nodeSchema["ui:label"];
  const icon = nodeSchema["ui:icon"] || "fa-solid fa-code-commit";

  const handleColorSelect = (e: MouseEvent, color: string | undefined) => {
    e.stopPropagation();
    if (onIconClick) {
      onIconClick(color);
    }
  };

  return (
      <div>
        <div className="dropdown dropdown-right absolute ml-2 mt-1 top-4 left-2">
          <div
            className="text-primary/70 tooltip tooltip-top cursor-pointer"
            data-tip={nodeSchema["ui:label"] + " (Click to change node color)"}
            tabIndex={0}
          >
            <i className={icon}></i>
          </div>
          <ul tabIndex={0} className="dropdown-content z-[1] menu p-2 shadow bg-base-100 rounded-box w-52">
            <li className="menu-title">Select color</li>
            {NODE_COLORS.map((color, index) => {
              const isCurrentColor = color.value === currentColor;

              return (
                <li key={index} onClick={(e) => handleColorSelect(e as unknown as MouseEvent, color.value)}>
                  <a className={`flex justify-between ${isCurrentColor ? 'bg-base-200' : ''}`}>
                    <span>{color.label}</span>
                    <span className={`w-6 h-6 rounded-full ${color.value} border`}></span>
                    {isCurrentColor && <i className="fa fa-check ml-2"></i>}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="px-10 m-1 text-lg font-bold text-center align-middle">
          <DeprecationNotice nodeSchema={nodeSchema}/>
          {header}
          <p className="text-xs font-light text-gray-500 dark:text-gray-600">{nodeId}</p>
        </div>
    </div>
  );
}


function DeprecationNotice({nodeSchema}: {nodeSchema: JsonSchema}) {
  if (!nodeSchema["ui:deprecated"]) {
    return <></>;
  }
  const customMessage = nodeSchema["ui:deprecation_message"] || "";
  return (
    <div className="dropdown">
      <div tabIndex={0} role="button" className="mr-2 text-warning inline-block tooltip hover:cursor-pointer"
      data-tip="This node type has been deprecated. Click for details"><i className="fa-solid fa-exclamation-triangle"></i></div>
      <div
        tabIndex={0}
        className="dropdown-content card card-sm bg-base-100 z-1 w-64 shadow-md">
        <div className="card-body">
          <p>This node type has been deprecated and will be removed in future.</p>
          {customMessage && <p dangerouslySetInnerHTML={{__html: customMessage}}></p>}
        </div>
      </div>
    </div>
  )
}
