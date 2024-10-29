import {Handle, Position, useUpdateNodeInternals} from "reactflow";
import React, {useEffect, useRef, useState} from "react";
import {NodeData, NodeParams} from "../types/nodeParams";
import {join} from "../utils";

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
  const ref = useRef<any>();
  const [position, setPosition] = useState(0);
  const updateNodeInternals = useUpdateNodeInternals()

  useEffect(() => {
    if (ref.current && ref.current.offsetTop && ref.current.clientHeight) {
      setPosition(ref.current.offsetTop + ref.current.clientHeight / 2)
      updateNodeInternals(nodeId)
    }
  }, [nodeId, ref, updateNodeInternals])

  useEffect(() => {
    updateNodeInternals(nodeId)
  }, [nodeId, position, updateNodeInternals])

  return <div ref={ref} className="py-2 text-right">
    <Handle
      id={handleKey}
      key={handleKey}
      type="source"
      position={Position.Right}
      style={{top: position}}
    />
    <span className="font-semibold font-mono">{label}</span>
  </div>
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
