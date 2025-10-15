import React, {DragEventHandler, MouseEventHandler} from "react";

type ComponentProps = {
  label: string;
  onClick: MouseEventHandler<HTMLDivElement>;
  onDragStart: DragEventHandler<HTMLDivElement>;
  parentRef: React.RefObject<HTMLDivElement | null>;
  hasHelp: boolean;
  toggleHelp: () => void;
}

export default function Component({label, onClick, onDragStart, parentRef, hasHelp, toggleHelp}: ComponentProps) {
  return (
    <div
      draggable={true}
      onClick={onClick}
      onDragStart={onDragStart}
      className="relative my-2 px-4 py-2 shadow-xs rounded-md border-2 border-stone-300 bg-base-100 hover:cursor-pointer hover:bg-slate-200 dark:hover:bg-slate-200/20"
    >
      <div className="m-1 text-center">
        <span className="font-bold">{label}</span>
        {hasHelp &&
          <div className="absolute top-0 right-0" ref={parentRef}>
            <button tabIndex={0} role="button" className="btn btn-circle btn-ghost btn-xs text-info"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleHelp();
                    }}
            >
              <i className={"fa-regular fa-circle-question"}></i>
            </button>
          </div>
        }
      </div>
    </div>
  );
}
