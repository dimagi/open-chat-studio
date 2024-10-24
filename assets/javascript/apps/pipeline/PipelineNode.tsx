import {Handle, Node, NodeProps, NodeToolbar, Position} from "reactflow";
import React, {ChangeEvent, useState} from "react";
import {classNames, getCachedData} from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import {InputParam} from "./types/nodeInputTypes";
import {NodeParams} from "./types/nodeParams";
import {getInputWidget} from "./nodes/GetInputWidget";
import {getOutputFactory} from "./nodes/GetOutputFactory";

type NodeData = {
  label: string;
  value: number;
  type: string;
  inputParams: InputParam[];
  params: NodeParams;
};

export type PipelineNode = Node<NodeData>;

export function PipelineNode({ id, data, selected }: NodeProps<NodeData>) {
  const cachedData = getCachedData();
  const defaultValues = cachedData.defaultValues;
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const defaultParams = data.inputParams.reduce(
    (acc, param) => {
      acc[param.name] = param.default || defaultValues[param.type];
      return acc;
    },
    {} as Record<string, any>,
  );
  const [params, setParams] = useState(data.params || defaultParams);

  const updateParamValue = (
    event: ChangeEvent<HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement>,
  ) => {
    const { name, value } = event.target;
    setParams((prevParams) => {
      const newParams = {
        ...prevParams,
        [name]: value,
      };
      setNode(id, (old) => ({
        ...old,
        data: {
          ...old.data,
          params: newParams,
        },
      }));
      return newParams;
    });
  };

  const handleFactory = getOutputFactory(data.type);

  return (
    <>
      <NodeToolbar position={Position.Top}>
        <div className="join">
          <button
            className="btn btn-xs join-item"
            onClick={() => deleteNode(id)}
          >
            <i className="fa fa-trash-o"></i>
          </button>
        </div>
      </NodeToolbar>
      <div
        className={classNames(
          selected ? "border border-primary" : "border",
          "px-4 py-2 shadow-md rounded-xl border-2 border-stone-400 bg-base-100",
        )}
      >
        <Handle type="target" position={Position.Left} id="input" />
        <div className="ml-2">
          <div className="m-1 text-lg font-bold text-center">{data.label}</div>
          {data.inputParams.map((inputParam) => (
            <React.Fragment key={inputParam.name}>
              {getInputWidget({id : id, inputParam : inputParam, params : params, setParams : setParams, updateParamValue : updateParamValue})}
            </React.Fragment>
          ))}
        </div>
        {handleFactory(params)}
      </div>
    </>
  );
}
