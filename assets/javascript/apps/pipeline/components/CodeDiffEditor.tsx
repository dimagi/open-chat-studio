import React, {useMemo} from "react";
import {python} from "@codemirror/lang-python";
import {unifiedMergeView} from "@codemirror/merge";
import {CodeMirrorEditor} from "./CodeMirrorEditor";

export function CodeDiffEditor(
  {original, value, onChange}: {
    original: string;
    value: string;
    onChange: (value: string) => void;
  }
) {
  const extensions = useMemo(() => [
    python(),
    // mergeControls is off: it renders its own per-chunk accept/reject buttons, which
    // would collide (in both label and semantics) with this panel's own whole-suggestion
    // Accept/Reject/Clear actions.
    unifiedMergeView({original, mergeControls: false, highlightChanges: true}),
  ], [original]);

  return <CodeMirrorEditor value={value} onChange={onChange} extensions={extensions}/>;
}
