import { Handle, Node, NodeProps, NodeToolbar, Position } from "reactflow";
import React, { ChangeEvent, useState } from "react";
import { classNames } from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParams } from "./types/nodeParams";

type NodeData = {
  label: string;
  value: number;
  inputParams: InputParam[];
  params: NodeParams;
};

export type PipelineNode = Node<NodeData>;

export function PipelineNode({ id, data, selected }: NodeProps<NodeData>) {
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const [params, setParams] = useState(data.params || {});

  const updateParamValue = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const { name, value } = event.target;
    setParams((prevParams) => {
      const newParams = {
        ...prevParams,
        [name]: value,
      };
      setNode(id, (old) => ({
        ...old,
        data: {
          ...old.data,
          params: newParams,
        },
      }));
      return newParams;
    });
  };

  return (
    <>
      <NodeToolbar position={Position.Top}>
        <div className="join">
          <button
            className="btn btn-xs join-item"
            onClick={() => deleteNode(id)}
          >
            <i className="fa fa-trash-o"></i>
          </button>
        </div>
      </NodeToolbar>
      <div
        className={classNames(
          selected ? "border border-primary" : "border",
          "w-100 rounded relative flex flex-col justify-center bg-base-100",
        )}
      >
        <Handle type="target" position={Position.Left} id="input" />
        <div className="m-1 text-center">{data.label}</div>
        {data.inputParams.map((inputParam) => (
          <React.Fragment key={inputParam.name}>
            <div className="m-1 text-center">{inputParam.name}</div>
            <textarea
              name={inputParam.name}
              onChange={updateParamValue}
              value={params[inputParam.name] || ""}
            ></textarea>
          </React.Fragment>
        ))}
        <Handle type="source" position={Position.Right} id="output" />
      </div>
    </>
  );
}
