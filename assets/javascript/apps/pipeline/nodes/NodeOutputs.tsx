import {Position} from "reactflow";
import React from "react";
import {NodeData, NodeParams} from "../types/nodeParams";
import {join} from "../utils";
import WrappedHandle from "./WrappedHandle";

export default function NodeOutputs({nodeId, data}: {nodeId: string, data: NodeData}) {
  const outputNames = getOutputNames(data.type, data.params);
  const multipleOutputs = outputNames.length > 1;
  return (
    <>
      {multipleOutputs && <div className="divider">Outputs</div>}
      <div className={multipleOutputs ? "": "py-2 mt-2 border-t border-neutral"}>
        {outputNames.map((outputName, index) => (
          <NodeOutput key={outputName} handleKey={`output_${index}`} nodeId={nodeId} label={outputName} />
        ))}
      </div>
    </>
  )
}

function NodeOutput({nodeId, handleKey, label}: {nodeId: string, handleKey: string, label: string}) {
  return <WrappedHandle
    nodeId={nodeId}
    id={handleKey}
    label={label}
    position={Position.Right}
    classes="py-2 text-right"
    key={handleKey}
    />
}


function getOutputNames(nodeType: string, params: NodeParams) {
  if (nodeType === "BooleanNode") {
    return ["Output True", "Output False"];
  } else if (nodeType === "RouterNode") {
    const numberOfOutputs = Math.max(1, parseInt(join(params.num_outputs)) || 1);
    return Array.from({length: numberOfOutputs}, (_, i) => {
      if (params.keywords && params.keywords[i]) {
        return `Keyword '${params.keywords[i]}'`
      }
      return `Output ${i + 1}`
    });
  } else {
    return ["Output"]
  }
}
