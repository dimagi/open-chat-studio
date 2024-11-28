import React from "react";
import { Handle, HandleProps } from "reactflow";
import {classNames} from "../utils";

/**
 * BaseHandle is a wrapper around the React Flow Handle component.
 * It forwards a ref and allows additional class names to be passed.
 *
 * Any classes or styling that apply to all handles should be added here.
 */
export const BaseHandle = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & HandleProps
>(({ className, ...props }, ref) => (
  <Handle ref={ref} className={classNames("", className)} {...props} />
));
BaseHandle.displayName = "BaseHandle";
