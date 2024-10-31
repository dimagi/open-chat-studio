import React from "react";
import {Position} from "reactflow";
import WrappedHandle from "./WrappedHandle";

export default function NodeInput({nodeId}: {nodeId: string}) {
  return <WrappedHandle
    nodeId={nodeId}
    id="input"
    label="Input"
    position={Position.Left}
    classes="py-2 mb-2 border-b border-neutral"
    />
}
