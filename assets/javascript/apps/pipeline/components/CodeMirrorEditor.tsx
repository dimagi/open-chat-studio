import {githubDarkInit, githubLightInit} from "@uiw/codemirror-theme-github";
import {ReactCodeMirrorProps} from "@uiw/react-codemirror/src";
import React, {useEffect, useState} from "react";
import CodeMirror, {EditorState} from "@uiw/react-codemirror";
import {autocompletion, Completion, CompletionContext, snippetCompletion as snip} from "@codemirror/autocomplete";
import {python} from "@codemirror/lang-python";
import {Decoration, DecorationSet, EditorView, ViewPlugin, ViewUpdate} from "@codemirror/view";

const githubDark = githubDarkInit({
  "settings": {
    background: "oklch(22% 0.016 252.42)",
    foreground: "oklch(97% 0.029 256.847)",
    fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
  }
})
const githubLight = githubLightInit({
  "settings": {
    fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
  }
})

export function CodeMirrorEditor(props: ReactCodeMirrorProps) {
  const [isDarkMode, setIsDarkMode] = useState(false);

  useEffect(() => {
    // Set dark / light mode
    setIsDarkMode(document.documentElement.getAttribute("data-theme") === 'dark')
    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        if (mutation.type === "attributes") {
          setIsDarkMode(document.documentElement.getAttribute("data-theme") === 'dark')
        }
      });
    });

    observer.observe(document.documentElement, {attributes: true});
    return () => observer.disconnect()
  }, []);

  const overrides = {
    className: "textarea textarea-bordered h-full w-full grow min-h-48",
    height: "100%",
    width: "100%",
    theme: isDarkMode ? githubDark : githubLight,
    basicSetup: {
      lineNumbers: true,
      tabSize: 4,
      indentOnInput: true,
    }
  }
  const p = {...props, ...overrides}
  return <CodeMirror {...p} />;
}

export function CodeNodeEditor(
  {value, onChange, readOnly}: {
    value: string;
    onChange: (value: string) => void;
    readOnly: boolean;
  }
) {
  const customCompletions = {
    get_participant_data: snip("get_participant_data()", {
      label: "get_participant_data",
      type: "function",
      detail: "Gets participant data for the current participant",
      boost: 1,
      section: "Participant Data"
    }),
    set_participant_data: snip("set_participant_data(${data})", {
      label: "set_participant_data",
      type: "function",
      detail: "Overwrites the participant data with the value provided",
      boost: 1,
      section: "Participant Data",
    }),
    set_participant_data_key: snip("set_participant_data_key(\"${key_name}\", ${data})", {
      label: "set_participant_data_key",
      type: "function",
      detail: "Overwrites the participant data at the specified key with the value provided",
      boost: 1,
      section: "Participant Data",
    }),
    append_to_participant_data_key: snip("append_to_participant_data_key(\"${key_name}\", ${data})", {
      label: "append_to_participant_data_key",
      type: "function",
      detail: "Appends the value to the participant data at the specified key",
      boost: 1,
      section: "Participant Data",
    }),
    increment_participant_data_key: snip("increment_participant_data_key(\"${key_name}\", ${data})", {
      label: "increment_participant_data_key",
      type: "function",
      detail: "Increments the value at the participant data at the specified key",
      boost: 1,
      section: "Participant Data",
    }),
    set_temp_state_key: snip("set_temp_state_key(\"${key_name}\", ${data})", {
      label: "set_temp_state_key",
      type: "function",
      detail: "Sets the given key in the temporary state. Overwrites the current value",
      boost: 1,
      section: "Temporary Data",
    }),
    get_temp_state_key: snip("get_temp_state_key(\"${key_name}\")", {
      label: "get_temp_state_key",
      type: "function",
      detail: "Gets the value for the given key from the temporary state",
      boost: 1,
      section: "Temporary Data",
    }),
    get_session_state: snip("get_session_state_key(\"${key_name}\")", {
      label: "get_session_state_key",
      type: "function",
      detail: "Gets the value for the given key from the session's state",
      boost: 1,
      section: "Session Data",
    }),
    set_session_state: snip("set_session_state_key(\"${key_name}\", ${data})", {
      label: "set_session_state_key",
      type: "function",
      detail: "Sets the given key in the session's state. Overwrites the current value",
      boost: 1,
      section: "Session Data",
    }),
    get_selected_route: snip("get_selected_route(\"${router_node_name}\")", {
      label: "get_selected_route",
      type: "function",
      detail: "Gets the route selected by a specific router node",
      boost: 1,
      section: "Routing"
    }),
    get_node_path: snip("get_node_path(\"${node_name}\")", {
      label: "get_node_path",
      type: "function",
      detail: "Gets the path (list of node names) leading to the specified node",
      boost: 1,
      section: "Routing"
    }),
    get_all_routes: snip("get_all_routes()", {
      label: "get_all_routes",
      type: "function",
      detail: "Gets all routing decisions in the pipeline",
      boost: 1,
      section: "Routing"
    }),
    get_node_output: snip("get_node_output(\"${node_name}\")", {
      label: "get_node_output",
      type: "function",
      detail: "Returns the output of the specified node if it has been executed. If the node has not been executed, it returns `None`.",
      boost: 1,
      section: "Node Outputs"
    }),

    add_message_tag: snip("add_message_tag(\"${tag_name}\")", {
      label: "add_message_tag",
      type: "function",
      detail: "Adds the tag to the output message",
      boost: 1
    }),

    add_session_tag: snip("add_session_tag(\"${tag_name}\")", {
      label: "add_session_tag",
      type: "function",
      detail: "Adds the tag to the chat session",
      boost: 1
    }),
    abort_with_message: snip("abort_with_message(\"${message}\", tag_name=\"${tag_name}\")", {
      label: "abort_with_message",
      type: "function",
      detail: "Terminates the pipeline execution. No further nodes will get executed in any branch of the pipeline graph.",
      boost: 1,
      section: "Flow Control",
    }),
    require_node_outputs: snip("require_node_outputs(${node_names})", {
      label: "require_node_outputs",
      type: "function",
      detail: "Ensures that the specified nodes have been executed and their outputs are available in the pipeline's state.",
      boost: 1,
      section: "Flow Control",
    }),
    get_output_voice: snip("get_output_voice()", {
      label: "get_output_voice",
      type: "function",
      detail: "Returns dict of voice name and is_default",
      boost: 1,
    }),
    set_output_voice: snip("set_output_voice(\"${voice}\")", {
      label: "set_output_voice",
      type: "function",
      detail: "Sets the output voice provider and voice. Enter voice in this format voice_provider:voice_name (openai:echo)",
      boost: 1,
    }),
  }

  function pythonCompletions(context: CompletionContext) {
    const word = context.matchBefore(/\w*/)
    if (!word || (word.from == word.to && !context.explicit))
      return null
    return {
      from: word.from,
      options: Object.values(customCompletions).filter(completion =>
        completion.label.toLowerCase().startsWith(word.text.toLowerCase())
      )
    }
  }

  let extensions = [
    python(),
    python().language.data.of({
      autocomplete: pythonCompletions
    })
  ];
  if (readOnly) {
    extensions = [
      ...extensions,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ]
  }

  return <CodeMirrorEditor value={value} onChange={onChange} extensions={extensions}/>;
}


export function PromptEditor(
  {value, onChange, readOnly, autocompleteVars}: {
    value: string;
    onChange: (value: string) => void;
    readOnly: boolean;
    autocompleteVars: string[];
  }
) {
  let extensions = [
    autocompletion({
      override: [textEditorVarCompletions(autocompleteVars)],
      activateOnTyping: true,
    }),
    highlightAutoCompleteVars(autocompleteVars),
    autocompleteVarTheme(),
    EditorView.lineWrapping,
  ];
  if (readOnly) {
    extensions = [
      ...extensions,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ]
  }
  return <CodeMirrorEditor value={value} onChange={onChange} extensions={extensions}/>;
}


function textEditorVarCompletions(autocompleteVars: string[]) {
  return (context: CompletionContext) => {
    const word = context.matchBefore(/[a-zA-Z0-9._[\]"]*$/);
    if (!word || (word.from === word.to && !context.explicit)) return null;

    return {
      from: word.from,
      options: autocompleteVars.map((v) => ({
        label: v,
        type: "variable",
        info: `Insert {${v}}`,
        apply: (view: EditorView, completion: Completion, from: number, to: number) => {
          const beforeText = view.state.doc.sliceString(from - 1, from);
          const insertText =
            beforeText === "{" ? `${completion.label}` : `{${completion.label}}`;
          view.dispatch({
            changes: {from, to, insert: insertText},
          });
        },
      })),
    };
  };
}

// highlight auto complete words. valid - blue, invalid - red
function highlightAutoCompleteVars(autocompleteVars: string[]) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.buildDecorations(view);
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this.buildDecorations(update.view);
        }
      }

      buildDecorations(view: EditorView) {
        const widgets: any[] = [];
        const text = view.state.doc.toString();
        const regex = /\{([a-zA-Z0-9._[\]"]+)\}/g;
        let match;
        while ((match = regex.exec(text)) !== null) {
          const varName = match[1];
          const from = match.index;
          const to = from + match[0].length;
          const isValidVar = autocompleteVars.some(
            v => varName === v || varName.startsWith(v + ".") || varName.startsWith(v + "[")
          );
          const deco = Decoration.mark({
            class: isValidVar
              ? "autocomplete-var-valid"
              : "autocomplete-var-invalid",
          });
          widgets.push(deco.range(from, to));
        }
        return Decoration.set(widgets);
      }
    },
    {
      decorations: (v) => v.decorations,
    }
  );
}

const autocompleteVarTheme = () =>
  EditorView.baseTheme({
    "&dark .autocomplete-var-valid": {
      color: "#93c5fd",
      fontWeight: "bold",
    },
    "&light .autocomplete-var-valid": {
      color: "navy",
      fontWeight: "bold",
    },
    "&dark .autocomplete-var-invalid": {
      color: "#f87171",
      fontWeight: "bold",
    },
    "&light .autocomplete-var-invalid": {
      color: "red",
      fontWeight: "bold",
    },
  });
