import React, {useMemo} from "react";
import {python} from "@codemirror/lang-python";
import {unifiedMergeView} from "@codemirror/merge";
import {Extension} from "@codemirror/state";
import {CodeMirrorEditor} from "./CodeMirrorEditor";

const PYTHON_EXTENSIONS = [python()];

export function CodeDiffEditor(
  {original, value, onChange, extensions = PYTHON_EXTENSIONS}: {
    original: string;
    value: string;
    onChange: (value: string) => void;
    extensions?: Extension[];
  }
) {
  const allExtensions = useMemo(() => [
    ...extensions,
    // mergeControls is off: it renders its own per-chunk accept/reject buttons, which
    // would collide (in both label and semantics) with this panel's own whole-suggestion
    // Accept/Reject/Clear actions.
    unifiedMergeView({original, mergeControls: false, highlightChanges: true}),
  ], [original, extensions]);

  return <CodeMirrorEditor value={value} onChange={onChange} extensions={allExtensions}/>;
}
