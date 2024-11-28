import {Position} from "reactflow";
import React from "react";
import {NodeData, NodeParams} from "../types/nodeParams";
import {concatenate} from "../utils";
import {LabeledHandle} from "./LabeledHandle";

export default function NodeOutputs({data}: {
  data: NodeData,
}) {
  const multipleOutputs = data.type === "RouterNode" || data.type === "BooleanNode";
  const outputNames = getOutputNames(data.type, data.params);
  const generateOutputHandle = (outputIndex: number) => {
    return multipleOutputs ? `output_${outputIndex}` : "output";
  };
  return (
    <>
      {multipleOutputs && <div className="divider">Outputs</div>}
      <div className={multipleOutputs ? "" : "py-2 mt-2 border-t border-neutral"}>
        {outputNames.map((output, index) => (
          <LabeledHandle
            id={generateOutputHandle(index)}
            key={index}
            title={output.label}
            type="source"
            position={Position.Right}
            labelClassName={output.isError ? "text-error" : "text-foreground"}
          />
        ))}
      </div>
    </>
  )
}


function getOutputNames(nodeType: string, params: NodeParams) {
  if (nodeType === "BooleanNode") {
    return [new Output("Output True"), new Output("Output False")];
  } else if (nodeType === "RouterNode") {
    const numberOfOutputs = Math.max(1, parseInt(concatenate(params.num_outputs)) || 1);
    return Array.from({length: numberOfOutputs}, (_, i) => {
      if (params.keywords?.[i]) {
        return new Output(params.keywords[i])
      }
      return new Output(`Output ${i + 1}`, true)
    });
  } else {
    return [new Output("Output")]
  }
}

class Output {
  constructor(readonly label: string, readonly isError: boolean = false) {
  }
}
