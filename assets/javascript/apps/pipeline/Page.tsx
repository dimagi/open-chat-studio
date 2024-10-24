import React, { ChangeEvent, useState } from "react";
import Pipeline from "./Pipeline";
import { NodeInputTypes } from "./types/nodeInputTypes";
import usePipelineManagerStore from "./stores/pipelineManagerStore";

export default function Page(props: { inputTypes: NodeInputTypes[] }) {
  const currentPipeline = usePipelineManagerStore(
    (state) => state.currentPipeline,
  );
  const savePipeline = usePipelineManagerStore((state) => state.savePipeline);
  const [name, setName] = useState(currentPipeline?.name);
  const handleNameChange = (event: ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value);
    if (currentPipeline) {
      usePipelineManagerStore.setState({
          currentPipeline: { ...currentPipeline, name: event.target.value },
        });
    }
  };
  const onClickSave = () => {
    if (currentPipeline) {
      savePipeline(currentPipeline);
    }
  };
  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-1">
        <div className="h-full w-full">
          <div className="grid grid-cols-6">
            <div className="col-span-5">
              <input
                type="text"
                value={name}
                onChange={handleNameChange}
                className="input input-bordered"
                placeholder="Edit pipeline name"
              />
            </div>
            <div className="justify-self-end">
              <button onClick={onClickSave} className="pg-button-primary mt-2">
                Save
              </button>
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
