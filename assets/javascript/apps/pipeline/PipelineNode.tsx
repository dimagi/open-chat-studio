import {Handle, Node, NodeProps, Position} from 'reactflow';
import React, {useState} from "react";
import {classNames} from "./utils";

type NodeData = {
  label: string,
  value: number;
};

export type PipelineNode = Node<NodeData>;

export function PipelineNode({data, selected}: NodeProps<NodeData>) {
  const [value, setValue] = useState(data?.value ?? 0)

  return (
    <div className={classNames(
          selected ? "border border-primary" : "border",
          "w-32 rounded relative flex flex-col justify-center bg-base-100"
        )}>
      <Handle type="target" position={Position.Left} id="input"/>
      <div className="m-1 text-center">{data.label}</div>
      <div className="m-1 text-center">{value}</div>
      <div className="btn btn-xs" onClick={() => setValue(value + 1)}>+</div>
      <Handle type="source" position={Position.Right} id="output"/>
    </div>
  )
}
