import React, {DragEventHandler} from "react";

export default function Component({
                                    label,
                                    onDragStart,
                                    parentRef,
                                    toggleHelp,
                                  }: {
  label: string;
  onDragStart: DragEventHandler<HTMLDivElement>;
  parentRef: React.RefObject<HTMLDivElement>;
  toggleHelp: () => void;
}) {
  return (
    <div
      draggable={true}
      onDragStart={onDragStart}
      className="relative my-2 px-4 py-2 shadow-sm rounded-md border-2 border-stone-300 bg-base-100 hover:cursor-pointer hover:bg-slate-200 dark:hover:bg-opacity-20"
    >
      <div className="m-1 text-center">
        <span className="font-bold">{label}</span>
        <div className="absolute top-0 right-0" ref={parentRef}>
          <div className="dropdown" onClick={toggleHelp}>
            <div tabIndex={0} role="button" className="btn btn-circle btn-ghost btn-xs text-info">
              <svg
                tabIndex={0}
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                className="h-4 w-4 stroke-current">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                  d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
              </svg>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
