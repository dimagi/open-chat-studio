import {basicSetup} from "codemirror"
import {EditorView} from "@codemirror/view"
import {linter, lintGutter, diagnosticCount} from "@codemirror/lint"
import {json, jsonParseLinter} from "@codemirror/lang-json"
import {Compartment, EditorState} from "@codemirror/state"

// Create a map to store editor instances by element
const editorInstances = new Map();

// Helper method to destroy an editor
EditorView.prototype.destroy = function() {
  this.dom.remove();
}

export const createJsonEditor = (element) => {
  // Check if editor already exists for this element
  if (editorInstances.has(element)) {
    // Destroy the existing editor before creating a new one
    editorInstances.get(element).destroy();
    editorInstances.delete(element);
  }

  let initialValue = "";
  let target = null;
  let disableElt = null;
  const disableEltQuery = element.getAttribute('data-disable-elt')
  if (disableEltQuery) {
    disableElt = document.querySelector(disableEltQuery)
  }
  const targetField = element.getAttribute('data-target-field')
  if (targetField) {
    target = document.querySelector(targetField)
    if (target) {
      initialValue = target.value
      target.style.display = 'none'
    }
  } else {
    console.error('json-editor element missing data-target-field attribute')
  }

  const errorContainer = document.createElement('div');
  errorContainer.className = 'json-editor-error text-error text-sm mt-1';
  element.parentNode.insertBefore(errorContainer, element.nextSibling);

  element.addEventListener('htmx:beforeRequest', () => {
    view.dispatch({
      effects: readOnly.reconfigure(EditorState.readOnly.of(true))
    })
  });

  element.addEventListener('htmx:afterRequest', () => {
    view.dispatch({
      effects: readOnly.reconfigure(EditorState.readOnly.of(false))
    })
  });

  const updateErrorStatus = (view) => {
    const errors = diagnosticCount(view.state);
    if (disableElt) {
      disableElt.disabled = errors > 0
    }
    if (errors > 0) {
      errorContainer.textContent = `Invalid JSON: ${errors} error(s) found`;
    } else {
      errorContainer.textContent = '';
    }
  }
  let timeout = null;
  let readOnly = new Compartment
  const view = new EditorView({
    doc: initialValue || "",
    parent: element,
    extensions: [
      basicSetup,
      json(),
      linter(jsonParseLinter(), {delay: 250}),
      lintGutter(),
      readOnly.of(EditorState.readOnly.of(false)),
      EditorView.updateListener.of((v) => {
        if (target && v.docChanged) {
          target.value = v.state.doc.toString()
          if (target._x_model) {
            target._x_model.set(target.value)
          }
          if (timeout) {
            clearTimeout(timeout)
          }
          updateErrorStatus(view)
          // wait until linter has finished
          timeout = setTimeout(() => {
            updateErrorStatus(view)
          }, 500)
          target.dispatchEvent(new Event('change', {bubbles: true}))
        }
      })
    ]
  })

  // Store the editor instance
  editorInstances.set(element, view);
  return view
}

export const initJsonEditors = () => {
  Array.from(document.getElementsByClassName('json-editor')).forEach(el => {
    createJsonEditor(el)
  })
}

// Global HTMX handler for reinitializing editors
document.addEventListener("htmx:afterSettle", (e) => {
  // Find editors in the swapped content
  const newEditors = e.detail.target.querySelectorAll('.json-editor');
  if (newEditors.length) {
    Array.from(newEditors).forEach(el => {
      createJsonEditor(el);
    });
  }
});

export const destroyAllEditors = () => {
  editorInstances.forEach(editor => editor.destroy());
  editorInstances.clear();
}
