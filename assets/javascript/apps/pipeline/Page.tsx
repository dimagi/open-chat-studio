import React, {ChangeEvent, useState} from "react";
import Pipeline from "./Pipeline";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import usePipelineStore from "./stores/pipelineStore";

export default function Page() {
  const currentPipeline = usePipelineManagerStore((state) => state.currentPipeline);
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);

  const updatePipelineName = usePipelineManagerStore((state) => state.updatePipelineName);
  const savePipeline = usePipelineManagerStore((state) => state.savePipeline);
  const dirty = usePipelineManagerStore((state) => state.dirty);
  const isSaving = usePipelineManagerStore((state) => state.isSaving);
  const [name, setName] = useState(currentPipeline?.name);
  const [editingName, setEditingName] = useState(false);
  const handleNameChange = (event: ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value);
    updatePipelineName(event.target.value);
  };
  const onClickSave = () => {
    if (currentPipeline) {
      const updatedPipeline = {...currentPipeline, data: {nodes, edges}}
      savePipeline(updatedPipeline).then(() => setEditingName(false));
    }
  };
  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-1">
        <div className="h-full w-full">
          <div className="grid grid-cols-2">
            <div className="flex gap-2">
              {editingName ? (
                <>
                  <input
                    type="text"
                    value={name}
                    onChange={handleNameChange}
                    className="input input-bordered input-sm"
                    placeholder="Edit pipeline name"
                  />
                  <button className="btn btn-sm btn-primary" onClick={onClickSave}>
                    <i className="fa fa-check"></i>
                  </button>
                </>
              ) : (
                <>
                  <div className="text-lg font-bold">{name}</div>
                  <button className="btn btn-sm btn-ghost" onClick={() => setEditingName(true)}>
                    <i className="fa fa-pencil"></i>
                  </button>
                </>
              )}
              <div className="tooltip tooltip-right" data-tip={dirty ? (isSaving ? "Saving ..." : "Preparing to Save") : "Saved"}>
                <button className="btn btn-sm btn-circle no-animation self-center">
                  {dirty ?
                    (isSaving ? <div className="loader loader-sm ml-2"></div> :
                      <i className="fa fa-cloud-upload"></i>)
                    : <i className="fa fa-check"></i>
                  }
                </button>
              </div>
            </div>
          </div>
          <div id="react-flow-id" className="relative h-full w-full">
            <Pipeline />
          </div>
        </div>
      </div>
    </div>
  );
}
