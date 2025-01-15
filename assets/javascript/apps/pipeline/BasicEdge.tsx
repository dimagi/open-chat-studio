import React, {type FC} from "react";
import {BaseEdge, type Edge, type EdgeProps, getBezierPath,} from "reactflow";
import usePipelineManagerStore from "./stores/pipelineManagerStore";

const BasicEdge: FC<EdgeProps<Edge>> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
}) => {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const hasError = usePipelineManagerStore((state) => state.edgeHasErrors(id));
  const style = hasError ? { stroke: "red", strokeWidth: 2 } : {};
  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} />
    </>
  );
};

export default BasicEdge;
