import React, { ReactNode } from "react";

import { NodeProps, NodeToolbar, Position } from "reactflow";
import { NodeData } from "./types/nodeParams";
import { nodeBorderClass } from "./utils";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import { BaseHandle } from "./nodes/BaseHandle";
import { HelpContent } from "./panel/ComponentHelp";

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
  const nodeError = usePipelineManagerStore((state) => state.getNodeFieldError(id, "root"));
  return (
    <>
      <NodeToolbar position={Position.Top} isVisible={!!nodeError}>
        <div className="border border-primary join">
            {nodeError && (
              <div className="dropdown dropdown-top">
                  <button tabIndex={0} role="button" className="btn btn-xs join-item">
                      <i className="fa-solid fa-exclamation-triangle text-warning"></i>
                  </button>
                  <HelpContent><p>{nodeError}</p></HelpContent>
              </div>
            )}
        </div>
      </NodeToolbar>
      <div className={nodeBorderClass(!!nodeError, selected)}>
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
