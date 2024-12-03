import React, { ReactNode } from "react";

import { NodeProps, Position } from "reactflow";
import { NodeData } from "./types/nodeParams";
import { nodeBorderClass } from "./utils";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import { BaseHandle } from "./nodes/BaseHandle";

function BoundaryNode({
  nodeProps,
  label,
  children,
}: {
  nodeProps: NodeProps<NodeData>;
  label: string;
  children: ReactNode;
}) {
  const { id, selected } = nodeProps;
  const nodeErrors = usePipelineManagerStore((state) => state.errors[id]);
  return (
    <>
      <div className={nodeBorderClass(nodeErrors, selected)}>
        <div className="px-4">
          <div className="m-1 text-lg font-bold text-center">{label}</div>
        </div>
      </div>
      {children}
    </>
  );
}

export function StartNode(nodeProps: NodeProps<NodeData>) {
  return (
    <BoundaryNode nodeProps={nodeProps} label="Input">
      <BaseHandle
        position={Position.Right}
        type="source"
        id="output"
        title="output"
      />
    </BoundaryNode>
  );
}

export function EndNode(nodeProps: NodeProps<NodeData>) {
  return (
    <BoundaryNode nodeProps={nodeProps} label="Output">
      <BaseHandle type="target" position={Position.Left} />
    </BoundaryNode>
  );
}
