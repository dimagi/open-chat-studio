import {Handle, Position, useUpdateNodeInternals} from "reactflow";
import React, {useEffect, useRef, useState} from "react";

export default function NodeInput({nodeId}: {nodeId: string}) {
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

  return (
    <div ref={ref} className="py-2 mb-2 border-b border-neutral">
      <Handle
        type="target"
        position={Position.Left} id="input"
        style={{top: position}}
      />
      <span className="font-semibold font-mono">Input</span>
    </div>
  )
}
