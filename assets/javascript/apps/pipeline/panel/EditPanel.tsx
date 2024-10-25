import React, {ChangeEvent, useEffect, useState} from "react";
import useEditorStore from "../stores/editorStore";
import OverlayPanel from "../components/OverlayPanel";
import {classNames, getCachedData} from "../utils";
import usePipelineStore from "../stores/pipelineStore";
import {getInputWidget} from "../nodes/GetInputWidget";
import {InputParam} from "../types/nodeInputTypes";


export default function EditPanel({nodeId}: { nodeId: string }) {
  const closeEditor = useEditorStore((state) => state.closeEditor);
  const getNode = usePipelineStore((state) => state.getNode);
  const [expanded, setExpanded] = useState(false);

  const {id, data} = getNode(nodeId)!;
  const cachedData = getCachedData();
  const defaultValues = cachedData.defaultValues;
  const setNode = usePipelineStore((state) => state.setNode);
  const getParams = () => {
    if (data.params) return data.params;
    return data.inputParams.reduce(
      (acc: Record<string, any>, param: InputParam) => {
        acc[param.name] = param.default || defaultValues[param.type];
        return acc;
      },
      {} as Record<string, any>,
    );
  }
  const [params, setParams] = useState(getParams());

  useEffect(() => {
    setNode(id!, (old) => ({
      ...old,
      data: {
        ...old.data,
        params: params,
      },
    }));
  }, [params]);

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const {name, value} = event.target;
    setParams((prevParams: Record<string, any>) => {
      return {...prevParams, [name]: value};
    });
  };

  const toggleExpand = () => {
    setExpanded(!expanded);
  }

  const width = expanded ? "w-full" : "w-2/5";

  return (
    <div className="relative">
      <OverlayPanel classes={classNames("top-0 right-0 h-[80vh] overflow-y-auto", width)} isOpen={true}>
        <>
          <div className="absolute top-0 left-0">
            <button className="btn btn-xs btn-ghost" onClick={toggleExpand}>
              {expanded ? <i className="fa-solid fa-down-left-and-up-right-to-center"></i> :
                <i className="fa-solid fa-up-right-and-down-left-from-center"></i>}
            </button>
          </div>
          <div className="absolute top-0 right-0">
            <button className="btn btn-xs btn-ghost" onClick={closeEditor}>
              <i className="fa fa-times"></i>
            </button>
          </div>
          <h2 className="text-lg text-center font-bold">Editing {data?.label}</h2>

          <div className={classNames("ml-2", expanded ? "grid grid-cols-2 gap-4" : "")}>
            {data.inputParams.map((inputParam: InputParam) => (
              <React.Fragment key={inputParam.name}>
                {getInputWidget({
                  id: id!,
                  inputParam: inputParam,
                  params: params,
                  setParams: setParams,
                  updateParamValue: updateParamValue,
                })}
              </React.Fragment>
            ))}
          </div>
        </>
      </OverlayPanel>
    </div>
  );
}
