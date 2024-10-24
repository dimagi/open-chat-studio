import {NodeParams} from "../types/nodeParams";
import React from "react";
import {Handle, Position} from "reactflow";

/**
 * Returns the appropriate output factory function based on the node type.
 * @param {string} nodeType - The type of the node.
 * @returns {(params: NodeParams) => React.JSX.Element} The output factory function for the specified node type.
 */
export const getOutputFactory = (nodeType: string) => {
  const outputFactories: Record<string, (params: NodeParams) => React.JSX.Element> = {
    BooleanNode: booleanOutputs,
    RouterNode: routerOutputs,
  };
  return outputFactories[nodeType] || defaultOutputs;
};

/**
 * Generates a default output handle for nodes that do not have specific output factories.
 * @returns {JSX.Element} A single output handle positioned at the right.
 */
const defaultOutputs = () => {
  return <Handle key="output_1" type="source" position={Position.Right} id="output"></Handle>;
}

/**
 * Generates output handles for a router node based on the number of outputs specified in the parameters.
 * @param {NodeParams} params - The parameters for the node, including the number of outputs.
 * @returns {JSX.Element} A set of output handles positioned evenly along the right side of the node.
 */
const routerOutputs = (params: NodeParams) => {
  const numberOfOutputs =
    Math.max(1, parseInt(
      Array.isArray(params.num_outputs)
        ? params.num_outputs.join("")
        : params.num_outputs,
    ) || 1);
  const outputHandles = Array.from(
    {length: numberOfOutputs},
    (_, index) => {
      const spacing = 100 / (numberOfOutputs + 1);
      const position = 50 + (index - (numberOfOutputs - 1) / 2) * spacing;  // Evenly spaced outputs around the center
      const handleAnnotation = handleText(`Output ${index + 1}`)

      return (
        <Handle
          key={`output_${index}`}
          type="source"
          position={Position.Right}
          style={{top: `${position}%`}}
          id={`output_${index}`}
        >
          {handleAnnotation}
        </Handle>
      );
    },
  );
  return <>{outputHandles}</>;
};

/**
 * Generates output handles for a boolean node.
 * @returns {JSX.Element} A set of output handles for true and false values.
 */
const booleanOutputs = () => {
  const outputHandles = [
    <Handle
      key="output_false"
      type="source"
      position={Position.Right}
      style={{top: "75%"}}
      id="output_false"
    >
      {handleText("Output False")}
    </Handle>,
    <Handle
      key="output_true"
      type="source"
      position={Position.Right}
      style={{top: "25%"}}
      id="output_true"
    >
      {handleText("Output True")}
    </Handle>
  ];

  return <>{outputHandles}</>;
};

const handleText = (label: string) => {
  return <div className="font-bold relative w-24 translate-x-[15px] -translate-y-[15px] pointer-events-none whitespace-nowrap">
    {label}
  </div>;
}
