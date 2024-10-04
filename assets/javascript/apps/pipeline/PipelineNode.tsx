import { Handle, Node, NodeProps, NodeToolbar, Position } from "reactflow";
import React, { ChangeEvent, useState } from "react";
import { classNames } from "./utils";
import usePipelineStore from "./stores/pipelineStore";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParams } from "./types/nodeParams";
import {
  KeywordsWidget,
  LlmModelWidget,
  LlmProviderIdWidget,
  SourceMaterialIdWidget,
  TextWidget,
} from "./widgets";
import { NodeParameterValues } from "./types/nodeParameterValues";

type NodeData = {
  label: string;
  value: number;
  type: string;
  inputParams: InputParam[];
  params: NodeParams;
};

export type PipelineNode = Node<NodeData>;

export function PipelineNode({ id, data, selected }: NodeProps<NodeData>) {
  const parameterValues: NodeParameterValues = JSON.parse(
    document.getElementById("parameter-values")?.textContent || "{}",
  );
  const defaultValues = JSON.parse(
    document.getElementById("default-values")?.textContent || "{}",
  );
  const setNode = usePipelineStore((state) => state.setNode);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const defaultParams = data.inputParams.reduce(
    (acc, param) => {
      acc[param.name] = defaultValues[param.type];
      return acc;
    },
    {} as Record<string, any>,
  );
  const [params, setParams] = useState(data.params || defaultParams);

  const updateParamValue = (
    event: ChangeEvent<
      HTMLTextAreaElement | HTMLSelectElement | HTMLInputElement
    >,
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

  const getInputWidget = (inputParam: InputParam) => {
    switch (inputParam.type) {
      case "LlmTemperature":
        return (
          <>
            <div className="m-1 font-medium text-center">Temperature</div>
            <input
              className="input input-bordered w-full"
              name={inputParam.name}
              onChange={updateParamValue}
              value={params[inputParam.name]}
              type="number"
              step=".1"
            ></input>
          </>
        );
      case "LlmProviderId":
        return (
          <>
            <div className="m-1 font-medium text-center">LLM Provider</div>
            <LlmProviderIdWidget
              parameterValues={parameterValues}
              inputParam={inputParam}
              value={params[inputParam.name]}
              setParams={setParams}
              id={id}
            />
          </>
        );
      case "SourceMaterialId":
        return (
          <>
            <div className="m-1 font-medium text-center">Source Material</div>
            <SourceMaterialIdWidget
              parameterValues={parameterValues}
              onChange={updateParamValue}
              inputParam={inputParam}
              value={params[inputParam.name]}
            />
          </>
        );
      case "LlmModel":
        return (
          <>
            <div className="m-1 font-medium text-center">LLM Model</div>
            <LlmModelWidget
              parameterValues={parameterValues}
              inputParam={inputParam}
              value={params[inputParam.name]}
              onChange={updateParamValue}
              provider={
                Array.isArray(params.llm_provider_id)
                  ? params.llm_provider_id.join("")
                  : params.llm_provider_id
              }
            />
          </>
        );
      case "NumOutputs":
        return (
          <>
            <div className="m-1 font-medium text-center">Number of Outputs</div>
            <input
              className="input input-bordered w-full"
              name={inputParam.name}
              onChange={updateParamValue}
              value={params[inputParam.name] || 1}
              type="number"
              step="1"
            ></input>
          </>
        );
      case "Keywords": {
        const length =
          parseInt(
            Array.isArray(params.num_outputs)
              ? params.num_outputs.join("")
              : params.num_outputs,
          ) || 1;
        return (
          <>
            {Array.from({ length: length }, (_, index) => {
              return (
                <KeywordsWidget
                  index={index}
                  keywords={
                    Array.isArray(params.keywords) ? params["keywords"] : []
                  }
                  setParams={setParams}
                  id={id}
                  key={`${inputParam.name}-${index}`}
                ></KeywordsWidget>
              );
            })}
          </>
        );
      }
      default: {
        const humanName = inputParam.human_name
          ? inputParam.human_name
          : inputParam.name.replace(/_/g, " ");
        return (
          <>
            <div className="m-1 font-medium text-center capitalize">
              {humanName}
            </div>
            <TextWidget
              humanName={humanName}
              name={inputParam.name}
              onChange={updateParamValue}
              value={params[inputParam.name] || ""}
            ></TextWidget>
          </>
        );
      }
    }
  };
  const getOuputHandles = () => {
    const numberOfOutputs =
      parseInt(
        Array.isArray(params.num_outputs)
          ? params.num_outputs.join("")
          : params.num_outputs,
      ) || 1;
    const outputHandles = Array.from(
      { length: numberOfOutputs },
      (_, index) => {
        const position = (index / (numberOfOutputs - 1)) * 100; // Distributes evenly between 0% to 100%
        return (
          <Handle
            key={`output_${index}`}
            type="source"
            position={Position.Right}
            style={{ top: `${position}%` }}
            id={numberOfOutputs > 1 ? `output_${index}` : "output"}
          />
        );
      },
    );
    return <>{outputHandles}</>;
  };

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
          "px-4 py-2 shadow-md rounded-md border-2 border-stone-400 bg-base-100",
        )}
      >
        <Handle type="target" position={Position.Left} id="input" />
        <div className="ml-2">
          <div className="m-1 text-lg font-bold text-center">{data.label}</div>
          {data.inputParams.map((inputParam) => (
            <React.Fragment key={inputParam.name}>
              {getInputWidget(inputParam)}
            </React.Fragment>
          ))}
        </div>
        {getOuputHandles()}
      </div>
    </>
  );
}
