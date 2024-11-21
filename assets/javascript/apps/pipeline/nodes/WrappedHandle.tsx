import {Handle, Position, useUpdateNodeInternals} from "reactflow";
import React, {useEffect, useRef, useState} from "react";

export default function WrappedHandle(props: {
  nodeId: string,
  label: string,
  classes: string,
  id: string,
  position: Position,
  parentBounds?: DOMRect
}) {
  const [position, setPosition] = useState(0);
  const ref = useRef<any>();
  const updateNodeInternals = useUpdateNodeInternals()

  useEffect(() => {
    if (ref.current && ref.current.offsetTop && ref.current.clientHeight) {
      setPosition(ref.current.offsetTop + ref.current.clientHeight / 2)
      updateNodeInternals(props.nodeId)
    }
  }, [props.nodeId, props.parentBounds, updateNodeInternals])

  return (
    <div ref={ref} className={props.classes}>
      <Handle
        type={props.position == Position.Left ? "target" : "source"}
        position={props.position}
        id={props.id}
        style={{top: position}}
      />
      <span className="font-semibold font-mono">{props.label}</span>
    </div>
  )
}
