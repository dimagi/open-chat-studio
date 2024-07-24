import React, { DragEventHandler } from "react";

export default function Component({
  label,
  onDragStart,
}: {
  label: string;
  onDragStart: DragEventHandler<HTMLDivElement>;
}) {
  return (
    <div
      draggable={true}
      onDragStart={onDragStart}
      className="my-2 px-4 py-2 shadow-md rounded-md border-2 border-stone-400 bg-base-100 cursor-move"
    >
      <div className="m-1 text-md font-bold text-center">{label}</div>
    </div>
  );
}
