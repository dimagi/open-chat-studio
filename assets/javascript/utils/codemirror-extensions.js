import { EditorView, ViewPlugin, Decoration, } from "@codemirror/view";

export function textEditorVarCompletions(autocompleteVars) {
  return (context) => {
    const word = context.matchBefore(/[a-zA-Z0-9._[\]"]*$/);
    if (!word || (word.from === word.to && !context.explicit)) return null;

    return {
      from: word.from,
      options: autocompleteVars.map((v) => ({
        label: v,
        type: "variable",
        info: `Insert {${v}}`,
        apply: (view, completion, from, to) => {
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

// Tool names go into the prompt as plain text: braces are reserved for prompt variables.
export function toolNameCompletions(tools) {
  return (context) => {
    const word = context.matchBefore(/[A-Za-z0-9_-]*$/);
    if (!word) return null;
    if (word.to - word.from < 2 && !context.explicit) return null;
    if (context.state.doc.sliceString(word.from - 1, word.from) === "{") return null;

    // Offered only when the typed word starts the name or one of its parts, not on CodeMirror's
    // fuzzy match, which pops a tool up for ordinary words ("the" in "weather_get") and lets Enter
    // replace the word.
    const typed = word.text.toLowerCase();
    const matches = tools.filter((tool) =>
      [tool.name, ...tool.name.split(/[-_]/)].some((part) => part.toLowerCase().startsWith(typed))
    );
    if (matches.length === 0) return null;

    return {
      from: word.from,
      filter: false,
      options: matches.map((tool) => ({
        label: tool.name,
        type: "function",
        detail: tool.label,
        // No `section`: CodeMirror ranks every sectioned option below every unsectioned one.
        info: tool.note ?? "Tool enabled on this node. Inserted as plain text.",
      })),
    };
  };
}

// highlight auto complete words. valid - blue, invalid - red
export function highlightAutoCompleteVars(autocompleteVars) {
  return ViewPlugin.fromClass(
    class {
      constructor(view) {
        this.decorations = this.buildDecorations(view);
      }

      update(update) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this.buildDecorations(update.view);
        }
      }

      buildDecorations(view) {
        const widgets = [];
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

export const autocompleteVarTheme = () =>
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
