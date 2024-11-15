import React, {useEffect, useState} from "react";

export default function ComponentHelp({label, parentRef, scrollPosition, showHelp, children}: {
  label: string,
  parentRef: React.RefObject<HTMLDivElement>,
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
    <div className="card compact dropdown-content bg-base-100 rounded-box z-20 w-64 shadow"
         style={{position: "fixed", top: top, left: left}}>
      <div tabIndex={0} className="card-body">
        <h2 className="font-semibold">{label}</h2>
        {children}
      </div>
    </div>
  );
}
