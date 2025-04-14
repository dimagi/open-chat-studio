import React, {ChangeEvent, useState} from "react";
import useEditorStore from "../stores/editorStore";
import OverlayPanel from "../components/OverlayPanel";
import {classNames, getCachedData} from "../utils";
import usePipelineStore from "../stores/pipelineStore";
import {getWidgets} from "../nodes/GetInputWidget";
import {produce} from "immer"

export default function EditPanel({nodeId}: { nodeId: string }) {
  const closeEditor = useEditorStore((state) => state.closeEditor);
  const getNode = usePipelineStore((state) => state.getNode);
  const setNode = usePipelineStore((state) => state.setNode);

  const [expanded, setExpanded] = useState(false);

  const {id, data} = getNode(nodeId)!;

  const nodeSchema = getCachedData().nodeSchemas.get(data.type)!;
  const schemaProperties = Object.getOwnPropertyNames(nodeSchema.properties);

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const {name, type} = event.target;
    const value = type === 'checkbox' ? (event.target as HTMLInputElement).checked : event.target.value;
    if (!schemaProperties.includes(name)) {
      console.warn(`Unknown parameter: ${name}`);
      return;
    }
    setNode(
    id!,
    produce((next) => {
      next.data.params[name] = value;
    })
  );
};

  const toggleExpand = () => {
    setExpanded(!expanded);
  }

  const width = expanded ? "w-full" : "w-2/5";
  return (
    <div className="relative">
      <OverlayPanel classes={classNames("top-0 right-0 h-[80vh] overflow-y-auto", width)} isOpen={true}
        onOpenChange={(value) => !value && closeEditor()}>
        <>
          <div className="sticky top-0 z-10 p-2 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
            <div className="absolute top-0 left-0">
              <button className="btn btn-xs btn-ghost" onClick={toggleExpand}
                aria-label={expanded ? "Collapse panel" : "Expand panel"}
              >
                {expanded ? <i className="fa-solid fa-down-left-and-up-right-to-center"></i> :
                  <i className="fa-solid fa-up-right-and-down-left-from-center"></i>}
              </button>
            </div>
            <div className="absolute top-0 right-0">
              <button className="btn btn-xs btn-ghost" onClick={closeEditor} aria-label="Close editor">
                <i className="fa fa-times"></i>
              </button>
            </div>
            <h2 className="text-lg text-center font-bold">{`Editing ${nodeSchema["ui:label"]}`}</h2>
          </div>
          <div className="p-4 ml-2">
            {schemaProperties.length === 0 && (
              <p className="pg-text-muted">No parameters to edit</p>
            )}
            {getWidgets({schema: nodeSchema, nodeId: id!, nodeData: data, updateParamValue})}
          </div>
        </>
      </OverlayPanel>
    </div>
  );
}
