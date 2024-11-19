import React, {useEffect, useState} from "react";
import Component from "./Component";
import {NodeInputTypes} from "../types/nodeInputTypes";
import OverlayPanel from "../components/OverlayPanel";
import {getCachedData} from "../utils";
import ComponentHelp from "./ComponentHelp";

type ComponentListParams = {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}

export default function ComponentList({isOpen, setIsOpen}: ComponentListParams) {
  const {inputTypes, defaultValues} = getCachedData();

  function getDefaultParamValues(inputType: NodeInputTypes): Record<string, any> {
    return inputType.input_params.reduce(
      (acc, param) => {
        acc[param.name] = param.default || defaultValues[param.type] || null;
        return acc;
      },
      {} as Record<string, any>,
    );
  }

  //** Help bubble state
  const [scrollPosition, setScrollPosition] = useState(0)

  const refMap: Record<string, React.RefObject<HTMLDivElement>> = {};
  inputTypes.forEach((inputType) => {
    refMap[inputType.name] = React.createRef<HTMLDivElement>();
  });

  const [showHelp, setShowHelp] = useState({
    show: new Map(inputTypes.map((inputType) => [inputType.name, false]))
  })
  const toggleHelp = (inputType: NodeInputTypes) => {
    setShowHelp(({show}) => {
      const newShow = new Map(inputTypes.map((type) => [type.name, false]));
      newShow.set(inputType.name, !show.get(inputType.name));
      return { show: newShow };
    });
  }
  const hideHelp = () => {
    setShowHelp(() => {
      return {show: new Map(inputTypes.map((inputType) => [inputType.name, false]))};
    });
  };

  useEffect(hideHelp, [scrollPosition, isOpen]);
  //** end help bubble state

  function onDragStart(
    event: React.DragEvent<any>,
    inputType: NodeInputTypes
  ): void {
    hideHelp();
    const nodeData = {
      label: inputType.human_name,
      inputParams: inputType.input_params,
      type: inputType.name,
      params: getDefaultParamValues(inputType),
    }
    event.dataTransfer.setData("nodedata", JSON.stringify(nodeData));
  }

  function togglePanel() {
    setIsOpen(!isOpen);
  }

  const components = inputTypes.map((inputType) => {
    return (
      <Component
        key={inputType.name}
        label={inputType.human_name}
        onDragStart={(event) =>
          onDragStart(event, inputType)
        }
        parentRef={refMap[inputType.name]}
        hasHelp={!!inputType.node_description}
        toggleHelp={() => toggleHelp(inputType)}
      />
    );
  });

  // Help bubbles need to be outside the overlay container to avoid clipping
  const helps = inputTypes.map((inputType) => {
    if (!inputType.node_description) {
      return null;
    }
    return <ComponentHelp
      key={inputType.name}
      label={inputType.human_name}
      parentRef={refMap[inputType.name]}
      scrollPosition={scrollPosition}
      showHelp={showHelp.show.get(inputType.name)}
    >
      <p>{inputType.node_description}</p>
    </ComponentHelp>;
  })


  const onscroll = (event: React.UIEvent<HTMLElement>) => {
    setScrollPosition(event.currentTarget.scrollTop)
  }

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

      <OverlayPanel classes="top-16 left-4 w-72 max-h-[70vh] overflow-y-auto" isOpen={isOpen} onScroll={onscroll}>
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
      {helps}
    </div>
  );
}
