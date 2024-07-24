import React from "react";
import Component from "./Component";
import { NodeParams } from "../types/nodeParams";
import { NodeInputTypes } from "../types/nodeInputTypes";

export default function SidePanel(props: { inputTypes: NodeInputTypes[] }) {
  function onDragStart(
    event: React.DragEvent<any>,
    data: { type: string; label: string; inputParams: NodeParams[] },
  ): void {
    event.dataTransfer.setData("nodedata", JSON.stringify(data));
  }

  return (
    <div className="w-full">
      <h2 className="text-xl text-center font-bold">Available Components</h2>
      Drag into the workflow editor to use
      {props.inputTypes.map((inputType) => {
        return (
          <Component
            key={inputType.name}
            label={inputType.human_name}
            onDragStart={(event) =>
              onDragStart(event, {
                label: inputType.human_name,
                inputParams: inputType.input_params,
                type: inputType.name,
              })
            }
          />
        );
      })}
    </div>
  );
}
