import React, {useEffect, useMemo, useRef, useState} from "react";
import {EditorView} from "@codemirror/view";
import {apiClient} from "../api/api";
import {CodeDiffEditor} from "../components/CodeDiffEditor";
import {autocompleteVarTheme, highlightAutoCompleteVars} from "../../../utils/codemirror-extensions.js";

type Proposal = {
  prompt: string;
  notes: string[];
}

type ImprovePromptSectionParams = {
  show: boolean;
  currentPrompt: string;
  nodeType: "llm" | "router";
  toolNames: string[];
  routes: string[];
  defaultRoute: string;
  autocompleteVars: string[];
  onAccept: (value: string) => void;
}

export default function ImprovePromptSection(
  {show, currentPrompt, nodeType, toolNames, routes, defaultRoute, autocompleteVars, onAccept}: ImprovePromptSectionParams
) {
  const [instruction, setInstruction] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Bumped when a request starts, when the user discards the proposal and when the prompt changes,
  // so a response to a request that is no longer current is ignored.
  const requestRevisionRef = useRef(0);

  // A proposal, or a pending request, was made from the prompt as it was then. Once the prompt
  // is edited, accepting it would overwrite the edit.
  useEffect(() => {
    requestRevisionRef.current += 1;
    setBusy(false);
    setProposal(null);
  }, [currentPrompt]);

  const diffExtensions = useMemo(
    () => [EditorView.lineWrapping, highlightAutoCompleteVars(autocompleteVars), autocompleteVarTheme()],
    [autocompleteVars]
  );

  const improve = () => {
    if (busy) return;
    const revision = ++requestRevisionRef.current;
    setBusy(true);
    setError("");
    apiClient.improvePrompt({
      prompt: currentPrompt,
      node_type: nodeType,
      tool_names: toolNames,
      routes,
      default_route: defaultRoute,
      instruction,
    }).then((result) => {
      if (revision !== requestRevisionRef.current) return;
      setBusy(false);
      if (result.error || !result.response?.prompt) {
        setError(result.error || "No suggestion was returned. Please try again.");
        return;
      }
      setProposal(result.response);
    }).catch((errorData) => {
      if (revision !== requestRevisionRef.current) return;
      setBusy(false);
      setError(errorData?.error || "An error occurred while improving the prompt. Please try again.");
    });
  };

  const discard = () => {
    requestRevisionRef.current += 1;
    setBusy(false);
    setProposal(null);
  };
  const handleAccept = () => {
    if (!proposal) return;
    onAccept(proposal.prompt);
    discard();
    setInstruction("");
    setError("");
  };
  const handleClear = () => {
    discard();
    setInstruction("");
    setError("");
  };

  const handleKeydown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      improve();
    }
  };

  if (!show) {
    return null;
  }

  const hasChanges = proposal !== null && proposal.prompt !== currentPrompt;
  return (
    <div className="my-2">
      <input
        type="text"
        className="input input-sm w-full nodrag"
        placeholder="What should change? Leave empty for a general review"
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onKeyDown={handleKeydown}
      />
      {error && <small className="text-red-500">{error}</small>}
      <div className="flex items-center gap-2 my-2">
        <div className="join">
          <button type="button" className="btn btn-sm btn-primary join-item" onClick={improve} disabled={!currentPrompt || busy}>
            <i className="fa-solid fa-wand-magic-sparkles"></i>Improve
          </button>
          <button type="button" className="btn btn-sm btn-ghost join-item" onClick={handleClear}>
            <i className="fa-solid fa-trash"></i>Clear
          </button>
        </div>
        {busy && <span className="loading loading-bars loading-md"></span>}
        {busy && <span className="text-sm text-gray-500">Reviewing the prompt...</span>}
      </div>
      {proposal !== null && (
        <div>
          {proposal.notes.length > 0 && (
            <ul className="list-disc pl-5 text-sm mb-2">
              {proposal.notes.map((note, index) => <li key={index}>{note}</li>)}
            </ul>
          )}
          {hasChanges && (
            <>
              <h2 className="font-semibold">Proposed changes</h2>
              <div className="max-h-[35vh] overflow-y-auto">
                <CodeDiffEditor
                  original={currentPrompt}
                  value={proposal.prompt}
                  onChange={(prompt) => setProposal({...proposal, prompt})}
                  extensions={diffExtensions}
                />
              </div>
              <div className="my-2 join">
                <button type="button" className="btn btn-sm btn-success join-item" onClick={handleAccept}>
                  <i className="fa-solid fa-check"></i>
                  Accept
                </button>
                <button type="button" className="btn btn-sm btn-warning join-item" onClick={discard}>
                  <i className="fa-solid fa-xmark"></i>
                  Reject
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
