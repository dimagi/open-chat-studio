import {Handle, Position, useUpdateNodeInternals} from "reactflow";
import React, {useEffect, useRef, useState} from "react";
import {classNames} from "../utils";

export default function WrappedHandle(props: {
  nodeId: string,
  label: string,
  classes: string,
  id: string,
  position: Position,
  parentBounds?: DOMRect
  isError: boolean
}) {
  const [position, setPosition] = useState(null);
  const ref = useRef<any>();
  const updateNodeInternals = useUpdateNodeInternals()

  useEffect(() => {
    if (ref.current && ref.current.offsetTop && ref.current.clientHeight) {
      setPosition(ref.current.offsetTop + ref.current.clientHeight / 2)
      updateNodeInternals(props.nodeId)
    }
  }, [ref, props.parentBounds, updateNodeInternals])

  const style = position != null ? {top: position} : {}
  return (
    <div ref={ref} className={props.classes}>
      <Handle
        type={props.position == Position.Left ? "target" : "source"}
        position={props.position}
        id={props.id}
        style={style}
      />
      <span className={classNames("font-semibold font-mono", props.isError ? "text-error" : "")}>{props.label}</span>
    </div>
  )
}
