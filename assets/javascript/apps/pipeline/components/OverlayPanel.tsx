import React from "react";
import {classNames} from "../utils";
import {useHotkeys} from "react-hotkeys-hook";

type OverlayPanelProps = {
  isOpen: boolean;
  classes?: string;
  onOpenChange?: (isOpen: boolean) => void;
  onScroll?: (event: React.UIEvent<HTMLDivElement>) => void;
}

 export default function OverlayPanel(props: React.PropsWithChildren<OverlayPanelProps>) {
   const openClasses = props.isOpen ? "scale-100" : "opacity-0 scale-95 pointer-events-none";
 
   useHotkeys("Escape", () => {
     if (props.isOpen) {
       props.onOpenChange?.(false);
     }
   });

   return (
     <div
       role="dialog"
       aria-modal={props.isOpen}
       tabIndex={-1}
       className={classNames(
         "absolute bg-white dark:bg-gray-800 shadow-lg rounded-md p-4 z-20 transition-all duration-300 transform",
         "border border-gray-200 dark:border-gray-700",
         openClasses,
         props.classes,
       )}
       onScroll={props.onScroll}
     >
       {props.children}
     </div>
   );
 }
