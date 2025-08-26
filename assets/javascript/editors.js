import { basicSetup, minimalSetup } from "codemirror"
import { EditorView } from "@codemirror/view"
import { linter, lintGutter, diagnosticCount } from "@codemirror/lint"
import { json, jsonParseLinter } from "@codemirror/lang-json"
import { Compartment, EditorState } from "@codemirror/state"
import {MergeView} from "@codemirror/merge"
import "../styles/app/editors.css";

/**
 * JsonEditor - A class to manage CodeMirror JSON editor instances
 */
class JsonEditor {
  /** Map of all editor instances by DOM element */
  static instances = new Map();

  /** Editor configuration */
  readOnly = new Compartment();
  timeout = null;
  errorContainer = null;

  /**
   * Create a new JSON editor instance
   * @param {HTMLElement} element - DOM element to attach editor to
   */
  constructor(element) {
    this.element = element;
    this.setupTargets();
    this.createErrorContainer();
    this.setupEventListeners();
    this.createEditor();

    // Store the instance
    JsonEditor.instances.set(element, this);
  }

  /**
   * Find target elements for editor
   */
  setupTargets() {
    this.target = null;
    this.disableElement = null;
    this.initialValue = "";

    // Get form field target
    const targetField = this.element.getAttribute('data-target-field');
    if (targetField) {
      this.target = document.querySelector(targetField);
      if (this.target) {
        this.initialValue = this.target.value;
        this.target.style.display = 'none';
      }
    } else {
      console.error('json-editor element missing data-target-field attribute');
    }

    // Get disable element if specified
    const disableEltQuery = this.element.getAttribute('data-disable-elt');
    if (disableEltQuery) {
      this.disableElement = document.querySelector(disableEltQuery);
    }
  }

  /**
   * Create error message container
   */
  createErrorContainer() {
    this.errorContainer = document.createElement('div');
    this.errorContainer.className = 'json-editor-error text-error text-sm mt-1 transition-opacity duration-200 opacity-0';
    this.element.parentNode.insertBefore(this.errorContainer, this.element.nextSibling);
  }

  /**
   * Set up HTMX event listeners
   */
  setupEventListeners() {
    this.element.addEventListener('htmx:beforeRequest', () => {
      if (this.view) {
        this.element.classList.add('opacity-60', 'cursor-not-allowed');
        this.view.dispatch({
          effects: this.readOnly.reconfigure(EditorState.readOnly.of(true))
        });
      }
    });

    this.element.addEventListener('htmx:afterRequest', () => {
      if (this.view) {
        this.element.classList.remove('opacity-60', 'cursor-not-allowed');
        this.view.dispatch({
          effects: this.readOnly.reconfigure(EditorState.readOnly.of(false))
        });
      }
    });
    // reset event listener
    const form = this.element.closest('form');
    if (form) {
      form.addEventListener('reset', () => {
        setTimeout(() => this.reset(), 10);
      });
    }
  }

  /**
   * Format the current JSON content
   */
  formatJSON() {
    try {
      // Get current content
      const content = this.view.state.doc.toString();

      // Parse and re-stringify with formatting
      const formatted = JSON.stringify(JSON.parse(content), null, 2);

      // Replace editor content
      this.view.dispatch({
        changes: {
          from: 0,
          to: this.view.state.doc.length,
          insert: formatted
        }
      });

      // Show success indicator
      const indicator = document.createElement('div');
      indicator.className = 'absolute right-2 bottom-2 bg-success text-white text-xs py-1 px-2 rounded-md opacity-0 transition-opacity';
      indicator.textContent = 'Formatted';
      indicator.style.zIndex = '10';
      this.element.appendChild(indicator);

      // Animate in then out
      setTimeout(() => indicator.classList.remove('opacity-0'), 10);
      setTimeout(() => {
        indicator.classList.add('opacity-0');
        setTimeout(() => indicator.remove(), 300);
      }, 1500);

      return true;
    } catch (e) {
      // Update error container
      this.errorContainer.textContent = `Format failed: ${e.message}`;
      this.errorContainer.classList.remove('opacity-0');

      setTimeout(() => {
        this.errorContainer.classList.add('opacity-0');
      }, 3000);

      return false;
    }
  }

  /**
   * Create CodeMirror editor instance
   */
  createEditor() {
    // Add Tailwind styling to the editor container
    this.element.classList.add('border', 'rounded-md', 'overflow-hidden', 'shadow-xs', 'focus-within:ring-2', 'focus-within:ring-primary-focus', 'bg-base-100', 'relative');

    // Create format button
    const formatBtn = document.createElement('button');
    formatBtn.className = 'absolute bottom-2 right-2 z-10 text-xs bg-base-200 hover:bg-base-300 text-base-content px-2 py-1 rounded-sm transition-colors';
    formatBtn.textContent = 'Format';
    formatBtn.addEventListener('click', (e) => {
      e.preventDefault();
      this.formatJSON();
    });
    this.element.appendChild(formatBtn);

    this.view = new EditorView({
      doc: this.initialValue || "",
      parent: this.element,
      extensions: [
        basicSetup,
        json(),
        linter(jsonParseLinter(), { delay: 250 }),
        lintGutter(),
        this.readOnly.of(EditorState.readOnly.of(false)),
        EditorView.updateListener.of(this.handleEditorUpdate.bind(this)),
        EditorView.theme({
          "&": {
            fontSize: "0.9rem",
            height: "100%",
            minHeight: "10rem",
            maxHeight: "20rem"
          },
        }),
        // Keyboard shortcut for formatting
        EditorView.domEventHandlers({
          keydown: (e) => {
            // Alt+Shift+F for formatting
            if (e.altKey && e.shiftKey && e.key === 'F') {
              this.formatJSON();
              return true;
            }
            return false;
          }
        })
      ]
    });
  }

  /**
   * Handle editor content updates
   * @param {*} update - CodeMirror update object
   */
  handleEditorUpdate(update) {
    if (this.target && update.docChanged) {
      this.target.value = update.state.doc.toString();

      // Handle Alpine.js integration
      if (this.target._x_model) {
        this.target._x_model.set(this.target.value);
      }

      // Clear existing timeout
      if (this.timeout) {
        clearTimeout(this.timeout);
      }

      // Update error status
      this.updateErrorStatus();

      this.timeout = setTimeout(() => {
        // Update error status after linter has finished
        this.updateErrorStatus();
      }, 500);

      // Trigger change event
      this.target.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  /**
   * Update error status display
   */
  updateErrorStatus() {
    const errors = diagnosticCount(this.view.state);

    // Update submit button state
    if (this.disableElement) {
      this.disableElement.disabled = errors > 0;
    }

    // Update error message
    if (errors > 0) {
      this.errorContainer.textContent = `Invalid JSON: ${errors} error(s) found`;
    } else {
      this.errorContainer.textContent = '';
    }
  }

  reset() {
    if (!this.view) return;

    this.view.dispatch({
      changes: {
        from: 0,
        to: this.view.state.doc.length,
        insert: this.initialValue || '{}'
      }
    });

    if (this.target) {
        this.target.value = this.initialValue || '{}';
    }
    this.updateErrorStatus();
  }

  /**
   * Destroy the editor instance
   */
  destroy() {
    if (this.view) {
      this.view.dom.remove();
      this.view = null;
    }

    if (this.errorContainer) {
      this.errorContainer.remove();
    }

    JsonEditor.instances.delete(this.element);
  }

  /**
   * Create or update a JSON editor for an element
   * @param {HTMLElement} element - DOM element to attach editor to
   * @returns {JsonEditor} The editor instance
   */
  static create(element) {
    // Clean up existing instance if present
    if (JsonEditor.instances.has(element)) {
      JsonEditor.instances.get(element).destroy();
    }

    return new JsonEditor(element);
  }

  /**
   * Initialize all editors matching a selector
   * @param {string} selector - CSS selector to find editor elements
   */
  static initAll(selector = '.json-editor') {
    Array.from(document.querySelectorAll(selector)).forEach(el => {
      JsonEditor.create(el);
    });
  }

  /**
   * Destroy all editor instances
   */
  static destroyAll() {
    JsonEditor.instances.forEach(editor => editor.destroy());
  }
}

// Initialize editors when the DOM is loaded
export const initJsonEditors = () => {
  JsonEditor.initAll();
};

// Create a single editor instance
export const createJsonEditor = (element) => {
  return JsonEditor.create(element);
};

// Cleanup all editors
export const destroyAllEditors = () => {
  JsonEditor.destroyAll();
};

// Global HTMX handler for reinitializing editors
document.addEventListener("htmx:afterSettle", (e) => {
  const newEditors = e.detail.target.querySelectorAll('.json-editor');
  if (newEditors.length) {
    Array.from(newEditors).forEach(el => {
      JsonEditor.create(el);
    });
  }
});


/**
 * createDiffView - Create a CodeMirror diff view for comparing two documents
 */
export const createDiffView = (docOriginal, docChanged, parent) => {
  return new MergeView({
    a: {
      doc: docOriginal,
      extensions: [
        minimalSetup,
        EditorView.lineWrapping,
        EditorView.editable.of(false),
        EditorState.readOnly.of(true),
      ]
    },
    b: {
      doc: docChanged,
      extensions: [
        minimalSetup,
        EditorView.lineWrapping,
        EditorView.editable.of(false),
        EditorState.readOnly.of(true),
      ]
    },
    parent: parent,
    gutter: false,
    collapseUnchanged: {margin: 2, minSize: 3},
  })
}

document.addEventListener("DOMContentLoaded", () => {
  const diffViews = document.querySelectorAll('.diff-view');
  if (diffViews.length) {
    Array.from(diffViews).forEach(el => {
      createDiffView(
        el.getAttribute('data-diff-a'),
        el.getAttribute('data-diff-b'),
        el
      )
    });
  }
});
