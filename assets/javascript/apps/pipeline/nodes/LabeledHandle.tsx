import React from "react";
import {HandleProps} from "reactflow";
import {classNames} from "../utils";
import {BaseHandle} from "./BaseHandle";

function getFlexDirection(position: string) {
  const flexDirection =
    position === "top" || position === "bottom" ? "flex-col" : "flex-row";
  switch (position) {
    case "bottom":
    case "right":
      return flexDirection + "-reverse justify-end";
    default:
      return flexDirection;
  }
}

/**
 * LabeledHandle is a wrapper around the BaseHandle component.
 * It displays a label next to the handle and allows additional class names to be passed.
 *
 * Taken from https://reactflow.dev/components/handles/labeled-handle
 */
const LabeledHandle = React.forwardRef<
  HTMLDivElement,
  HandleProps &
    React.HTMLAttributes<HTMLDivElement> & {
      label: string | React.ReactElement<any>;
      handleClassName?: string;
      labelClassName?: string;
    }
>(({ className, labelClassName, label, position, ...props }, ref) => (
  <div
    ref={ref}
    className={classNames(
      "relative flex items-center",
      getFlexDirection(position),
      className,
    )}
  >
    <BaseHandle position={position} {...props} />
    <label className={classNames("px-4 font-semibold font-mono", labelClassName)}>{label}</label>
  </div>
));

LabeledHandle.displayName = "LabeledHandle";

export { LabeledHandle };
