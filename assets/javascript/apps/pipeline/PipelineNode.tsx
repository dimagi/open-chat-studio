import {Node, NodeProps, NodeToolbar, Position} from "reactflow";
import React, {ChangeEvent} from "react";
import {getCachedData, nodeBorderClass} from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import useEditorStore from "./stores/editorStore";
import {NodeData} from "./types/nodeParams";
import {getNodeInputWidget, showAdvancedButton} from "./nodes/GetInputWidget";
import NodeInput from "./nodes/NodeInput";
import NodeOutputs from "./nodes/NodeOutputs";
import {HelpContent} from "./panel/ComponentHelp";

export type PipelineNode = Node<NodeData>;

export function PipelineNode(nodeProps: NodeProps<NodeData>) {
  const {id, data, selected} = nodeProps;
  const openEditorForNode = useEditorStore((state) => state.openEditorForNode)
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const nodeErrors = usePipelineManagerStore((state) => state.errors[id]);
  const {nodeSchemas} = getCachedData();
  const nodeSchema = nodeSchemas.get(data.type)!;
  const schemaProperties = Object.getOwnPropertyNames(nodeSchema.properties);
  const requiredProperties = nodeSchema.required || [];

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const {name, value} = event.target;
    setNode(id, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          [name]: value,
        },
      },
    }));
  };

  const editNode = () => {
    openEditorForNode(nodeProps);
  }

  return (
    <>
      <NodeToolbar position={Position.Top}>
        <div className="border border-primary join">
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
            {nodeSchema.description && (
              <div className="dropdown dropdown-top">
                  <button tabIndex={0} role="button" className="btn btn-xs join-item">
                      <i className={"fa fa-circle-question"}></i>
                  </button>
                  <HelpContent><p>{nodeSchema.description}</p></HelpContent>
              </div>
            )}
        </div>
      </NodeToolbar>
      <div className={nodeBorderClass(nodeErrors, selected)}>
        <div className="m-1 text-lg font-bold text-center">{nodeSchema["ui:label"]}</div>

        <NodeInput />
        <div className="px-4">
          <div>
            {schemaProperties.map((name) => (
              <React.Fragment key={name}>
                {getNodeInputWidget({
                  id: id,
                  name: name,
                  schema: nodeSchema.properties[name],
                  params: data.params,
                  updateParamValue: updateParamValue,
                  nodeType: data.type,
                  required: requiredProperties.includes(name),
                })}
              </React.Fragment>
            ))}
          </div>
          {showAdvancedButton(data.type) && (
            <div className="mt-2">
              <button className="btn btn-sm btn-ghost w-full"
                      onClick={() => editNode()}>
                Advanced
              </button>
            </div>
          )}
        </div>
        <NodeOutputs data={data} />
      </div>
    </>
  );
}
