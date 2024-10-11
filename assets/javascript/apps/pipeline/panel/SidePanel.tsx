import React, { useRef } from "react";
import Component from "./Component";
import { NodeParams } from "../types/nodeParams";
import { NodeInputTypes } from "../types/nodeInputTypes";

export default function SidePanel(props: {
  inputTypes: NodeInputTypes[];
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}) {
  const { inputTypes, isOpen, setIsOpen } = props;
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  function onDragStart(
    event: React.DragEvent<any>,
    data: { type: string; label: string; inputParams: NodeParams[] }
  ): void {
    event.dataTransfer.setData("nodedata", JSON.stringify(data));
  }

  function togglePanel() {
    setIsOpen(!isOpen);
  }

  return (
    <div className="relative">
      <button
        ref={buttonRef}
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

      <div
        ref={panelRef}
        className={`absolute top-16 left-4 w-72 max-h-[80vh] overflow-y-auto bg-white shadow-lg rounded-md p-4 z-20 transition-all duration-300 ${
          isOpen
            ? "opacity-100 transform scale-100"
            : "opacity-0 transform scale-95 pointer-events-none"
        }`}
      >
        {isOpen && (
          <>
            <h2 className="text-xl text-center font-bold">Available Components</h2>
            <p className="text-sm text-center mb-4">
              Drag into the workflow editor to use
            </p>
            {inputTypes.map((inputType) => {
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
          </>
        )}
      </div>
    </div>
  );
}
