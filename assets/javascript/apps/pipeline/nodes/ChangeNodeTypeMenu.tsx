import React from "react";
import usePipelineStore from "../stores/pipelineStore";
import {addableNodeSchemas} from "../utils";

/**
 * Toolbar dropdown that swaps a node for one of another type in place, keeping the edges it
 * can (see `changeNodeType` in the pipeline store).
 */
export default function ChangeNodeTypeMenu({nodeId, currentType}: {nodeId: string; currentType: string}) {
  const changeNodeType = usePipelineStore((state) => state.changeNodeType);
  const options = addableNodeSchemas().filter((schema) => schema.title !== currentType);

  if (!options.length) {
    return <></>;
  }

  return (
    <div className="dropdown dropdown-bottom">
      <button
        type="button"
        tabIndex={0}
        className="btn btn-xs join-item"
        title="Change node type"
        aria-label="Change node type"
      >
        <i className="fa-solid fa-right-left"></i>
      </button>
      <ul
        tabIndex={0}
        className="dropdown-content z-[1] menu p-2 shadow bg-base-100 rounded-box w-56 max-h-80 flex-nowrap overflow-y-auto"
      >
        <li className="menu-title">Change node type</li>
        {options.map((schema) => (
          <li key={schema.title}>
            <a onClick={() => changeNodeType(nodeId, schema.title)}>{schema["ui:label"]}</a>
          </li>
        ))}
      </ul>
    </div>
  );
}
