import {Handle, Node, NodeProps, Position} from 'reactflow';
import React, {useState} from "react";

type NodeData = {
  value: number;
};

export type CustomNode = Node<NodeData>;

export function MyCustomNode({ data }: NodeProps<NodeData>) {
  const [value, setValue] = useState(data?.value ?? 0)

  return (
      <>
      <Handle type="target" position={Position.Top} />
      <div className={"bg-white border p-2 rounded"}>
        <label htmlFor="text">Text:</label>
        <input id="text" name="text" onChange={() => setValue(value + 1)} className="nodrag" />
        <p>{value}</p>
      </div>
      <Handle type="source" position={Position.Bottom} id="a" />
    </>
  )
}
