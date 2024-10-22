import React, { ChangeEvent, useState } from "react";
import Pipeline from "./Pipeline";
import { NodeInputTypes } from "./types/nodeInputTypes";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import usePipelineStore from "./stores/pipelineStore";

export default function Page(props: { inputTypes: NodeInputTypes[] }) {
  const currentPipeline = usePipelineManagerStore((state) => state.currentPipeline);
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);
  const reactFlowInstance = usePipelineStore((state) => state.reactFlowInstance);

  const savePipeline = usePipelineManagerStore((state) => state.savePipeline);
  const lastSaved = usePipelineManagerStore((state) => state.lastSaved);
  const isSaving = usePipelineManagerStore((state) => state.isSaving);
  const [name, setName] = useState(currentPipeline?.name);
  const handleNameChange = (event: ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value);
    currentPipeline &&
      usePipelineManagerStore.setState({
        currentPipeline: { ...currentPipeline, name: event.target.value },
      });
  };
  const onClickSave = () => {
    if (currentPipeline) {
      const viewport = reactFlowInstance?.getViewport()!;
      const updatedPipeline = {...currentPipeline, data: {nodes, edges, viewport}}
      savePipeline(updatedPipeline);
    }
  };
  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-1">
        <div className="h-full w-full">
          <div className="grid grid-cols-2">
            <div className="">
              <input
                type="text"
                value={name}
                onChange={handleNameChange}
                className="input input-bordered"
                placeholder="Edit pipeline name"
              />
            </div>
            <div className="justify-self-end place-items-end">
              <button onClick={onClickSave} className="btn btn-primary btn-sm" disabled={isSaving}>
                {isSaving ? <div className="loader loader-sm ml-2"></div> : "Save"}
              </button>
              <div className="text-xs">Last saved: {lastSaved ? new Date(lastSaved).toLocaleString() : "Never"}</div>
            </div>
          </div>
          <div id="react-flow-id" className="relative h-full w-full">
            <Pipeline inputTypes={props.inputTypes} />
          </div>
        </div>
      </div>
    </div>
  );
}
