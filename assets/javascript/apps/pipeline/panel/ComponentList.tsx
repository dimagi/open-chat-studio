import React from "react";
import Component from "./Component";
import {InputParam, NodeInputTypes} from "../types/nodeInputTypes";
import OverlayPanel from "../components/OverlayPanel";

type ComponentListParams = {
  inputTypes: NodeInputTypes[];
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}

export default function ComponentList({ inputTypes, isOpen, setIsOpen }: ComponentListParams) {
  function onDragStart(
    event: React.DragEvent<any>,
    data: { type: string; label: string; inputParams: InputParam[] }
  ): void {
    event.dataTransfer.setData("nodedata", JSON.stringify(data));
  }

  function togglePanel() {
    setIsOpen(!isOpen);
  }

  const components = inputTypes.map((inputType) => {
    return (
      <Component
        key={inputType.name}
        label={inputType.human_name}
        nodeDescription={inputType.node_description}
        onDragStart={(event) =>
          onDragStart(event, {
            label: inputType.human_name,
            inputParams: inputType.input_params,
            type: inputType.name,
          })
        }
      />
    );
  });

  return (
    <div className="relative">
      <button
        className="absolute top-4 left-4 z-10 text-4xl text-primary"
        onClick={togglePanel}
        title="Add Node"
      >
        <i
          className={`fas ${
            isOpen ? "fa-circle-minus" : "fa-circle-plus"
          } text-4xl shadow-md rounded-full`}
        />
      </button>

      <OverlayPanel classes="top-16 left-4 w-72 max-h-[70vh] overflow-y-auto" isOpen={isOpen}>
        {isOpen && (
          <>
            <h2 className="text-xl text-center font-bold">Available Nodes</h2>
            <p className="text-sm text-center mb-4">
              Drag into the workflow editor to use
            </p>
            {components}
          </>
        )}
      </OverlayPanel>
    </div>
  );
}
