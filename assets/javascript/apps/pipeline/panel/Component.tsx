import React, {DragEventHandler} from "react";

type ComponentProps = {
  label: string;
  onDragStart: DragEventHandler<HTMLDivElement>;
  parentRef: React.RefObject<HTMLDivElement>;
  hasHelp: boolean;
  toggleHelp: () => void;
}

export default function Component({label, onDragStart, parentRef, hasHelp, toggleHelp}: ComponentProps) {
  return (
    <div
      draggable={true}
      onDragStart={onDragStart}
      className="relative my-2 px-4 py-2 shadow-sm rounded-md border-2 border-stone-300 bg-base-100 hover:cursor-pointer hover:bg-slate-200 dark:hover:bg-opacity-20"
    >
      <div className="m-1 text-center">
        <span className="font-bold">{label}</span>
        {hasHelp &&
          <div className="absolute top-0 right-0" ref={parentRef}>
            <button tabIndex={0} role="button" className="btn btn-circle btn-ghost btn-xs text-info"
                    onClick={toggleHelp}>
              <i className={"fa-regular fa-circle-question"}></i>
            </button>
          </div>
        }
      </div>
    </div>
  );
}
