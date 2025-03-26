import {basicSetup} from "codemirror"
import {EditorView} from "@codemirror/view"
import {linter, lintGutter, diagnosticCount} from "@codemirror/lint"
import {json, jsonParseLinter} from "@codemirror/lang-json"

export const createJsonEditor = (element) => {
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
  const view = new EditorView({
    doc: initialValue || "",
    parent: element,
    extensions: [
      basicSetup,
      json(),
      linter(jsonParseLinter(), {delay: 250}),
      lintGutter(),
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

  document.addEventListener("htmx:afterSwap", (e) => {
    // TODO: reinitialize editor after htmx swap
    console.log(e)
  })

  return view
}

export const initJsonEditors = () => {
  Array.from(document.getElementsByClassName('json-editor')).forEach(el => {
    createJsonEditor(el)
  })
}
