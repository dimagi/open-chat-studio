import React, { DragEventHandler } from "react";

export default function Component({
  label,
  nodeDescription,
  onDragStart,
}: {
  label: string;
  nodeDescription: string;
  onDragStart: DragEventHandler<HTMLDivElement>;
}) {
  return (
    <div
      draggable={true}
      onDragStart={onDragStart}
      className="my-2 px-4 py-2 shadow-sm rounded-md border-2 border-stone-300 bg-base-100 hover:cursor-pointer hover:bg-slate-200 dark:hover:bg-opacity-20"
    >
      <dl className="m-1 text-center">
        <dt className="font-bold">{label}</dt>
        <dd className="text-sm text-gray-400">{nodeDescription}</dd>
      </dl>
    </div>
  );
}
