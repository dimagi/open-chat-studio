import React, {useEffect, useState} from "react";
import Component from "./Component";
import OverlayPanel from "../components/OverlayPanel";
import {formatDocsForSchema, getCachedData} from "../utils";
import ComponentHelp from "./ComponentHelp";
import {JsonSchema, NodeData, NodeParams} from "../types/nodeParams";
import usePipelineStore from "../stores/pipelineStore";

type ComponentListParams = {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}

export default function ComponentList({isOpen, setIsOpen}: ComponentListParams) {
  const addNode = usePipelineStore((state) => state.addNode);
  const {defaultValues, nodeSchemas} = getCachedData();
  const schemaList = Array.from(nodeSchemas.values()).sort((a, b) => a["ui:label"].localeCompare(b["ui:label"]));

  function getDefaultParamValues(schema: JsonSchema): NodeParams {
    const defaults: NodeParams = {name: ""};
    for (const name in schema.properties) {
      const property = schema.properties[name];
      defaults[name] = [property.default, defaultValues[name]].find((value) => value !== undefined && value !== null) ?? null;
    }
    return defaults;
  }

  //** Help bubble state
  const [scrollPosition, setScrollPosition] = useState(0)

  const refMap = schemaList.reduce((acc, schema) => {
    acc[schema.title] = React.createRef();
    return acc;
  }, {} as Record<string, React.RefObject<HTMLDivElement | null>>);

  function getHelpOffState() {
    return new Map(Array.from(nodeSchemas.keys()).map((key) => [key, false]));
  }

  const [showHelp, setShowHelp] = useState({show: getHelpOffState()})
  const toggleHelp = (nodeType: string) => {
    setShowHelp(({show}) => {
      const newShow = getHelpOffState();
      newShow.set(nodeType, !show.get(nodeType));
      return {show: newShow};
    });
  }
  const hideHelp = () => {
    setShowHelp(() => {
      return {show: getHelpOffState()};
    });
  };

  useEffect(hideHelp, [scrollPosition, isOpen]);

  //** end help bubble state

  function onDragStart(
    event: React.DragEvent<any>,
    schema: JsonSchema
  ): void {
    hideHelp();
    const nodeData: NodeData = {
      type: schema.title,
      label: schema["ui:label"],
      flowType: schema["ui:flow_node_type"],
      params: getDefaultParamValues(schema),
    }
    event.dataTransfer.setData("nodedata", JSON.stringify(nodeData));
  }

  function onClick(
      event: React.MouseEvent<any>,
      schema: JsonSchema
  ): void {
      hideHelp();
      const newNode = {
          type: schema["ui:flow_node_type"],
          position: { x: 1000, y: 200 },
          data: {
              type: schema.title,
              label: schema["ui:label"],
              params: getDefaultParamValues(schema),
          },
      };
      addNode(newNode, { x: newNode.position.x, y: newNode.position.y });
      togglePanel();
  }

  function togglePanel() {
    setIsOpen(!isOpen);
  }

  const components = schemaList.filter((schema) => schema["ui:can_add"]).map((schema) => {
    return (
      <Component
        key={schema.title}
        label={schema["ui:label"]}
        onDragStart={(event) =>
          onDragStart(event, schema)
        }
        onClick={(event) => onClick(event, schema) }
        parentRef={refMap[schema.title]}
        hasHelp={!!schema.description}
        toggleHelp={() => toggleHelp(schema.title)}
      />
    );
  });

  // Help bubbles need to be outside the overlay container to avoid clipping
  const helps = schemaList.map((schema) => {
    const helpContent = formatDocsForSchema(schema);
    if (!helpContent) {
      return null;
    }
    return <ComponentHelp
      key={schema.title}
      label={schema["ui:label"]}
      parentRef={refMap[schema.title]}
      scrollPosition={scrollPosition}
      showHelp={showHelp.show.get(schema.title)}
    >
      {helpContent}
    </ComponentHelp>;
  })

  const onscroll = (event: React.UIEvent<HTMLElement>) => {
    setScrollPosition(event.currentTarget.scrollTop)
  }

  return (
    <div className="relative">
      <button
        className="btn btn-circle btn-ghost absolute top-4 left-4 z-10 text-primary"
        onClick={togglePanel}
        title="Add Node"
      >
        <i
          className={`fas ${
            isOpen ? "fa-circle-minus" : "fa-circle-plus"
          } text-4xl shadow-md rounded-full`}
        />
      </button>

      <OverlayPanel classes="p-4 top-16 left-4 w-72 max-h-[70vh] overflow-y-auto" isOpen={isOpen} onScroll={onscroll}>
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
