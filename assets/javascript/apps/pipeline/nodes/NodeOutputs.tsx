import {Position} from "reactflow";
import React from "react";
import {NodeData, NodeParams} from "../types/nodeParams";
import {LabeledHandle} from "./LabeledHandle";

export default function NodeOutputs({data}: {
  data: NodeData,
}) {
  const multipleOutputs = data.type === "RouterNode" || data.type === "BooleanNode" || data.type == "StaticRouterNode";
  const outputNames = getOutputNames(data.type, data.params);
  const generateOutputHandle = (outputIndex: number) => {
    return multipleOutputs ? `output_${outputIndex}` : "output";
  };
  const generateOutputLabel = (outputIndex: number, output_label:string) => {
    if (multipleOutputs && outputIndex === 0) {
      return (
        <span className="tooltip" data-tip="This is the default output if there are no matches">
          <i className="fa-solid fa-asterisk fa-2xs mr-1 text-accent"></i>{output_label}
        </span>
      );
    }
    return <>{output_label}</>;
  }
  return (
    <>
      {multipleOutputs && <div className="divider">Outputs</div>}
      <div className={multipleOutputs ? "" : "py-2 mt-2 border-t border-neutral"}>
        {outputNames.map((output, index) => (
          <LabeledHandle
            id={generateOutputHandle(index)}
            key={index}
            label={generateOutputLabel(index, output.label)}
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
  } else if (nodeType === "RouterNode" || nodeType == "StaticRouterNode") {
    const numberOfOutputs = Math.max(1, params.keywords?.length || 1);
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
