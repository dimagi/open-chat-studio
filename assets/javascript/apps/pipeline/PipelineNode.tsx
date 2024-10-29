import {Handle, Node, NodeProps, NodeToolbar, Position} from "reactflow";
import React, {ChangeEvent} from "react";
import {classNames} from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import useEditorStore from "./stores/editorStore";
import {NodeData} from "./types/nodeParams";
import {getNodeInputWidget, showAdvancedButton} from "./nodes/GetInputWidget";
import {getOutputFactory} from "./nodes/GetOutputFactory";

export type PipelineNode = Node<NodeData>;

export function PipelineNode(nodeProps: NodeProps<NodeData>) {
  const { id, data, selected } = nodeProps;
  const openEditorForNode = useEditorStore((state) => state.openEditorForNode)
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const { name, value } = event.target;
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

  const handleFactory = getOutputFactory(data.type);

  return (
    <>
      <NodeToolbar position={Position.Top}>
        <div className="border border-primary join">
          <button
            className="btn btn-xs join-item"
            onClick={() => deleteNode(id)}
          >
            <i className="fa fa-trash-o"></i>
          </button>
          <button
            className="btn btn-xs join-item"
            onClick={() => editNode()}
          >
            <i className="fa fa-pencil"></i>
          </button>
        </div>
      </NodeToolbar>
      <div
        className={classNames(
          selected ? "border border-primary" : "border",
          "px-4 py-2 shadow-md rounded-xl border-2 bg-base-100",
        )}
      >
        <Handle type="target" position={Position.Left} id="input" />
        <div className="ml-2">
          <div className="m-1 text-lg font-bold text-center">{data.label}</div>
          <div>
            {data.inputParams.map((inputParam) => (
              <React.Fragment key={inputParam.name}>
                {getNodeInputWidget({
                  id : id,
                  inputParam : inputParam,
                  params : data.params,
                  updateParamValue : updateParamValue,
                  nodeType: data.type,
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
        {handleFactory(data.params)}
      </div>
    </>
  );
}
