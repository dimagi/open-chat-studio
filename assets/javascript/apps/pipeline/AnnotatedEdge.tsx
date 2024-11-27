import React, { type FC } from "react";
import {
  getBezierPath,
  BaseEdge,
  EdgeLabelRenderer,
  type EdgeProps,
  type Edge,
} from "reactflow";

const AnnotatedEdge: FC<EdgeProps<Edge<{ label: string }>>> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
}) => {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={{ stroke: "green", strokeWidth: 2, fontWeight: "bold" }} />
      <EdgeLabelRenderer>
        <div
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
            borderColor: "green"
          }}
          className="nodrag nopan nowheel px-4 py-2 shadow-md rounded-xl border-2 bg-base-100 max-w-xs max-h-40 overflow-auto z-20"
        >
          {label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
};

export default AnnotatedEdge;
