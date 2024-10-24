import React from "react";
import {classNames} from "../utils";

type OverlayPanelProps = {
  isOpen: boolean;
  classes?: string;
}

export default function OverlayPanel(props: React.PropsWithChildren<OverlayPanelProps>) {
  const openClasses = props.isOpen ? "scale-100" : "opacity-0 scale-95 pointer-events-none";
  return (
    <div
      className={classNames(
        "absolute bg-white dark:bg-gray-800 shadow-lg rounded-md p-4 z-20 transition-all duration-300 transform",
        "border border-gray-200 dark:border-gray-700",
        openClasses,
        props.classes,
      )}
    >
       {props.children}
    </div>
  );
}
