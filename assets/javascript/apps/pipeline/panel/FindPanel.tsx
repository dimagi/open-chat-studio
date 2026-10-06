import React, {useMemo, useRef, useState} from "react";
import OverlayPanel from "../components/OverlayPanel";
import usePipelineStore from "../stores/pipelineStore";
import {classNames, getCachedData} from "../utils";
import {searchNodes, SearchMatch} from "../search";

type FindPanelParams = {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}

function Snippet({match}: {match: SearchMatch}) {
  const matchEnd = match.matchStart + match.matchLength;
  return (
    <span className="break-words">
      {match.snippet.slice(0, match.matchStart)}
      <mark>{match.snippet.slice(match.matchStart, matchEnd)}</mark>
      {match.snippet.slice(matchEnd)}
    </span>
  );
}

export default function FindPanel({isOpen, setIsOpen}: FindPanelParams) {
  const nodes = usePipelineStore((state) => state.nodes);
  const focusNode = usePipelineStore((state) => state.focusNode);
  const readOnly = usePipelineStore((state) => state.readOnly);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    const {nodeSchemas, flagsEnabled} = getCachedData();
    return searchNodes(nodes, nodeSchemas, query, flagsEnabled);
  }, [nodes, query]);
  const rows = results.flatMap((result) => result.matches.map((match) => ({nodeId: result.nodeId, field: match.field})));

  function updateQuery(value: string) {
    setQuery(value);
    setActiveIndex(0);
  }

  function moveActive(step: number) {
    if (rows.length === 0) return;
    const next = Math.min(Math.max(activeIndex + step, 0), rows.length - 1);
    setActiveIndex(next);
    listRef.current?.querySelector(`[data-row="${next}"]`)?.scrollIntoView?.({block: "nearest"});
  }

  function activate(index: number) {
    if (index >= rows.length) return;
    setActiveIndex(index);
    const {nodeId, field} = rows[index];
    // The node id is not a field in the editor.
    focusNode(nodeId, field === "id" ? undefined : field);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      activate(activeIndex);
    } else if (event.key === "Escape") {
      setIsOpen(false);
    }
  }

  const nodeCount = results.length;
  const summary = rows.length === 0
    ? "No matches"
    : `${rows.length} ${rows.length === 1 ? "match" : "matches"} in ${nodeCount} ${nodeCount === 1 ? "node" : "nodes"}`;

  let rowIndex = 0;
  return (
    <div className="relative">
      <button
        className={classNames(
          "btn btn-circle btn-ghost absolute top-4 z-10 text-primary",
          readOnly ? "left-4" : "left-28",
        )}
        onClick={() => setIsOpen(!isOpen)}
        title="Find in pipeline"
      >
        <i className="fas fa-magnifying-glass text-2xl" />
      </button>

      <OverlayPanel
        classes="p-4 top-16 left-4 w-96 max-h-[70vh] overflow-y-auto"
        isOpen={isOpen}
        onOpenChange={setIsOpen}
      >
        {isOpen && (
          <>
            <input
              type="text"
              className="input input-sm w-full"
              placeholder="Find in pipeline"
              aria-label="Find in pipeline"
              value={query}
              autoFocus
              onChange={(event) => updateQuery(event.target.value)}
              onKeyDown={onKeyDown}
            />
            {query.trim() && <p className="text-xs text-base-content/70 mt-2">{summary}</p>}
            <div ref={listRef} role="listbox" aria-label="Search results">
              {results.map((result) => (
                <div key={result.nodeId} className="mt-3">
                  <div className="text-sm font-bold">
                    {result.nodeName}
                    <span className="ml-2 font-normal text-base-content/70">{result.nodeLabel}</span>
                  </div>
                  {result.matches.map((match) => {
                    const index = rowIndex++;
                    return (
                      <div
                        key={index}
                        role="option"
                        aria-selected={index === activeIndex}
                        data-row={index}
                        className={classNames(
                          "cursor-pointer rounded-sm px-2 py-1 text-sm hover:bg-base-200",
                          index === activeIndex ? "bg-base-200" : "",
                        )}
                        onClick={() => activate(index)}
                      >
                        <span className="mr-2 text-xs text-base-content/70">{match.fieldLabel}</span>
                        <Snippet match={match} />
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>
        )}
      </OverlayPanel>
    </div>
  );
}
