import { Handle, Node, NodeProps, NodeToolbar, Position } from "reactflow";
import React, { useState } from "react";
import { classNames } from "./utils";
import usePipelineStore from "./stores/pipelineStore";

type NodeData = {
  label: string;
  value: number;
};

export type PipelineNode = Node<NodeData>;

export function PipelineNode({ id, data, selected }: NodeProps<NodeData>) {
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const [params, setParams] = useState(data.params || {});

  const updateParamValue = (event) => {
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
        {data.inputParams.map((param) => (
          <React.Fragment key={param.name}>
            <div className="m-1 text-center">{param.name}</div>
            <textarea
              name={param.name}
              onChange={updateParamValue}
              value={params[param.name] || ""}
            ></textarea>
          </React.Fragment>
        ))}
        <Handle type="source" position={Position.Right} id="output" />
      </div>
    </>
  );
}
