import React, {ChangeEvent, useState} from "react";
import Pipeline from "./Pipeline";
import usePipelineStore from "./stores/pipelineStore";

export default function Page() {
  const currentPipeline = usePipelineStore((state) => state.currentPipeline);
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);

  const updatePipelineName = usePipelineStore((state) => state.updatePipelineName);
  const savePipeline = usePipelineStore((state) => state.savePipeline);
  const dirty = usePipelineStore((state) => state.dirty);
  const isSaving = usePipelineStore((state) => state.isSaving);
  const error = usePipelineStore((state) => state.getPipelineError());
  const conflictDetected = usePipelineStore((state) => state.conflictDetected);
  const dismissConflict = usePipelineStore((state) => state.dismissConflict);
  const loadPipeline = usePipelineStore((state) => state.loadPipeline);
  const currentPipelineId = usePipelineStore((state) => state.currentPipelineId);
  const [name, setName] = useState(currentPipeline?.name);
  const [editingName, setEditingName] = useState(false);
  const handleNameChange = (event: ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value);
    updatePipelineName(event.target.value);
  };
  const allow_edit_name = JSON.parse(document.getElementById("allow-edit-name")?.textContent || "false");
  const readOnly = JSON.parse(document.getElementById("read-only")?.textContent || "false");

  const onClickSave = () => {
    if (currentPipeline) {
    const updatedPipeline = {
      ...currentPipeline,
      data: { nodes, edges },
    };
    savePipeline(updatedPipeline).then(() => setEditingName(false));
    }
  };
  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-1">
        <div className="h-full w-full">
          <div className="flex gap-2">
            {allow_edit_name &&
              (editingName ? (
              <>
                <input
                  type="text"
                  value={name}
                  onChange={handleNameChange}
                  className="input input-sm"
                  placeholder="Edit pipeline name"
                />
                <button className="btn btn-sm btn-primary" onClick={onClickSave}>
                  <i className="fa fa-check"></i>
                </button>
              </>
            ) : (
              <>
                <div className="text-lg font-bold">{name}</div>
                {!readOnly &&
                  <button className="btn btn-sm btn-ghost" onClick={() => setEditingName(true)}>
                    <i className="fa fa-pencil"></i>
                  </button>
                }
              </>
            ))}
            <div className="tooltip tooltip-right" data-tip={conflictDetected ? "Conflict - changes not saved" : dirty ? (isSaving ? "Saving ..." : "Preparing to Save") : "Saved"}>
              <button className="btn btn-sm btn-circle no-animation self-center">
                {conflictDetected ?
                  <i className="fa fa-exclamation-triangle text-amber-500"></i>
                  : dirty ?
                    (isSaving ? <span className="loading loading-spinner loading-xs"></span> :
                      <i className="fa fa-cloud-upload"></i>)
                    : <i className="fa fa-check"></i>
                }
              </button>
            </div>
            {!isSaving && error && (
              <div className="content-center">
                <i className="fa fa-exclamation-triangle text-red-500 mr-2"></i>
                <small className="text-red-500">{error}</small>
              </div>
            )}
            {readOnly &&  (
              <div className="content-center">
                <small>(Read-only)</small>
              </div>
            )}
          </div>
          {conflictDetected && (
            <div className="alert alert-warning rounded-none shadow-sm flex items-center justify-between" role="alert">
              <div className="flex items-center gap-2">
                <i className="fa fa-exclamation-triangle text-lg"></i>
                <span>This pipeline was modified in another session. Reload to see the latest version.</span>
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  className="btn btn-sm btn-warning"
                  onClick={() => {
                    dismissConflict();
                    if (currentPipelineId) {
                      loadPipeline(currentPipelineId);
                    }
                  }}
                >
                  Reload
                </button>
              </div>
            </div>
          )}
          <div id="react-flow-id" className="relative h-full w-full">
            <Pipeline />
          </div>
        </div>
      </div>
    </div>
  );
}
