import React from "react";
import {Position} from "reactflow";
import {LabeledHandle} from "./LabeledHandle";

export default function NodeInput({nodeId}: { nodeId: string }) {
  return <LabeledHandle type={"target"} position={Position.Left} title={"Input"}/>
}
