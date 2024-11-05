import React, {ChangeEvent, useState} from "react";
import useEditorStore from "../stores/editorStore";
import OverlayPanel from "../components/OverlayPanel";
import {classNames} from "../utils";
import usePipelineStore from "../stores/pipelineStore";
import {getInputWidget} from "../nodes/GetInputWidget";
import {InputParam} from "../types/nodeInputTypes";


export default function EditPanel({nodeId}: { nodeId: string }) {
  const closeEditor = useEditorStore((state) => state.closeEditor);
  const getNode = usePipelineStore((state) => state.getNode);
  const setNode = usePipelineStore((state) => state.setNode);

  const [expanded, setExpanded] = useState(false);

  const {id, data} = getNode(nodeId)!;

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const {name, value} = event.target;
    const param = data.inputParams.find((p: InputParam) => p.name === name);
    if (!param) {
      console.warn(`Unknown parameter: ${name}`);
      return;
    }
    setNode(id!, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: {
          ...old.data.params,
          [name]: value
        },
      },
    }));
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
          <h2 className="text-lg text-center font-bold">{data?.label ? `Editing ${data.label}` : 'Loading...'}</h2>

          <div className="ml-2">
            {data.inputParams.length === 0 && (
              <p className="pg-text-muted">No parameters to edit</p>
            )}
            {data.inputParams.map((inputParam: InputParam) => (
              <React.Fragment key={inputParam.name}>
                {getInputWidget({
                  id: id!,
                  inputParam: inputParam,
                  params: data.params,
                  updateParamValue: updateParamValue,
                  nodeType: data.type
                })}
              </React.Fragment>
            ))}
          </div>
        </>
      </OverlayPanel>
    </div>
  );
}