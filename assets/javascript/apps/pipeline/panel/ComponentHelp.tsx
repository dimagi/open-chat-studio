import React, {useEffect, useState} from "react";

export default function ComponentHelp({label, parentRef, scrollPosition, showHelp, children}: {
  label: string,
  parentRef: React.RefObject<HTMLDivElement | null>,
  scrollPosition: number
  showHelp: boolean | undefined
  children?: React.ReactNode
}) {
  const [top, setTop] = useState(0);
  const [left, setLeft] = useState(0);

  useEffect(() => {
    if (parentRef.current) {
      const boundingClientRect = parentRef.current.getBoundingClientRect();
      setTop(boundingClientRect.top)
      setLeft(boundingClientRect.right + 10)
    }
  }, [parentRef, scrollPosition])

  if (!showHelp) {
    return null;
  }
  return (
    <HelpContent style={{position: "fixed", top: top, left: left}}>
      <h2 className="font-semibold">{label}</h2>
      {children}
    </HelpContent>
  );
}

export function HelpContent(props: {
  children: React.ReactNode,
  style?: any
}) {
  return (
    <div className="card dropdown-content bg-base-100 rounded-box z-20 w-64 shadow-sm border border-neutral-500"
         style={props.style}>
      <div tabIndex={0} className="card-body">
        {props.children}
      </div>
    </div>
  )
}
