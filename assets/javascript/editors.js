import { basicSetup, minimalSetup } from "codemirror"
import { EditorView, keymap } from "@codemirror/view"
import { indentWithTab } from "@codemirror/commands"
import { linter, lintGutter, diagnosticCount } from "@codemirror/lint"
import { json, jsonParseLinter } from "@codemirror/lang-json"
import { indentUnit } from "@codemirror/language"
import { Compartment, EditorState } from "@codemirror/state"
import { githubDarkInit, githubLightInit } from "@uiw/codemirror-theme-github";
import {MergeView} from "@codemirror/merge"
import { python } from "@codemirror/lang-python"
import { textEditorVarCompletions, highlightAutoCompleteVars, autocompleteVarTheme } from "./utils/codemirror-extensions.js"
import { autocompletion } from "@codemirror/autocomplete"
import { find } from "./utils"
import "../styles/app/editors.css";

const githubDark = githubDarkInit({
    "settings": {
        background: "oklch(22% 0.016 252.42)",
        foreground: "oklch(97% 0.029 256.847)",
        fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
    }
});
const githubLight = githubLightInit({
    "settings": {
        fontFamily: 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"'
    }
});

const getSelectedTheme = () => {
    const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';
    return isDarkMode ? githubDark : githubLight;
};

class BaseEditor {
  /** Editor configuration */
  readOnly = new Compartment();
  timeout = null;
  errorContainer = null;

  /**
   * Create a new editor instance
   * @param {HTMLElement} element - DOM element to attach editor to
   * @param {Map} instanceMap - Static instances map for the editor type
   */
  constructor(element, instanceMap) {
    this.element = element;
    this.instanceMap = instanceMap;
    this.setupTargets();
    this.createErrorContainer();
    this.setupEventListeners();
    this.createEditor();

    // Store the instance
    this.instanceMap.set(element, this);

  }

  /**
   * Find target elements for editor
   */
  setupTargets() {
    this.target = null;
    this.disableElement = null;
    this.initialValue = "";

    // Get form field target
    let targetField = this.element.getAttribute('data-target-field');
    if (targetField) {
      this.target = find(this.element, targetField);
      if (this.target) {
        this.initialValue = this.target.value;
        this.target.style.display = 'none';
      }
    } else {
      console.error("Element missing data-target-field attribute", this.element);
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
    this.errorContainer.className = 'text-error text-sm mt-1 transition-opacity duration-200 opacity-0';
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
  }

  /**
   * Handle editor content updates
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

      // Update validation/error status if implemented
      if (this.updateErrorStatus) {
        this.updateErrorStatus();
        this.timeout = setTimeout(() => {
          this.updateErrorStatus();
        }, 500);
      }

      // Trigger change event
      this.target.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  /**
   * Get common extensions shared by all editors
   * @param {boolean} readOnlyValue - Whether the editor should be read-only
   * @returns {Array} Array of CodeMirror extensions
   */
  getCommonExtensions(readOnlyValue = false) {
    return [
      basicSetup,
      keymap.of([indentWithTab]),
      this.readOnly.of(EditorState.readOnly.of(readOnlyValue)),
      EditorView.updateListener.of(this.handleEditorUpdate.bind(this)),
      getSelectedTheme(),
    ];
  }

  /**
   * Abstract method to create the CodeMirror editor instance
   * Must be implemented by subclasses
   */
  createEditor() {
    throw new Error('createEditor() must be implemented by subclass');
  }

  /**
   * Destroy the editor instance
   */
  destroy() {
    if (this.view) {
      this.view.destroy();
      this.view = null;
    }

    if (this.errorContainer) {
      this.errorContainer.remove();
    }

    this.instanceMap.delete(this.element);
  }
}

/**
 * JsonEditor - A class to manage CodeMirror JSON editor instances
 */
class JsonEditor extends BaseEditor {
    errorClassName = 'json-editor-error';

  /** Map of all editor instances by DOM element */
  static instances = new Map();

  /**
   * Create a new JSON editor instance
   * @param {HTMLElement} element - DOM element to attach editor to
   */
  constructor(element) {
    super(element, JsonEditor.instances);
  }

  setupEventListeners() {
    super.setupEventListeners();
    // reset event listener
    const form = this.element.closest('form');
    if (form) {
      form.addEventListener('reset', () => {
        setTimeout(() => this.reset(), 10);
      });
    }
    this.element.addEventListener('resetEditor', () => {
      setTimeout(() => this.reset(), 10);
    })
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
        ...this.getCommonExtensions(false),
        json(),
        linter(jsonParseLinter(), { delay: 250 }),
        lintGutter(),
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

  updateValue(newValue) {
    if (!this.view) return;

    this.view.dispatch({
      changes: {
        from: 0,
        to: this.view.state.doc.length,
        insert: newValue
      }
    });
    this.updateErrorStatus();
  }

  reset() {
    const newValue = this.initialValue || '';
    this.updateValue(newValue);
    if (this.target) {
        this.target.value = newValue;
    }
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
  static initAll(parent, selector = '.json-editor') {
    parent = parent || document;
    Array.from(parent.querySelectorAll(selector)).forEach(el => {
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

/**
 * PythonEditor - A class to manage CodeMirror Python editor instances
 */
class PythonEditor extends BaseEditor {
  /** Map of all editor instances by DOM element */
  static instances = new Map();

  /**
   * Create a new Python editor instance
   * @param {HTMLElement} element - DOM element to attach editor to
   */
  constructor(element) {
    super(element, PythonEditor.instances);
  }

  /**
   * Create CodeMirror editor instance
   */
  createEditor() {
    const readOnlyAttr = this.element.hasAttribute('data-readonly');

    this.view = new EditorView({
      doc: this.initialValue || "",
      parent: this.element,
      extensions: [
        ...this.getCommonExtensions(readOnlyAttr),
        python(),
        indentUnit.of("    "),
        EditorView.lineWrapping,
      ]
    });
  }


  /**
   * Create or update a Python editor for an element
   * @param {HTMLElement} element - DOM element to attach editor to
   * @returns {PythonEditor} The editor instance
   */
  static create(element) {
    // Clean up existing instance if present
    if (PythonEditor.instances.has(element)) {
      PythonEditor.instances.get(element).destroy();
    }

    return new PythonEditor(element);
  }

  /**
   * Initialize all editors matching a selector
   * @param {string} selector - CSS selector to find editor elements
   */
  static initAll(selector = '.python-editor') {
    Array.from(document.querySelectorAll(selector)).forEach(el => {
      PythonEditor.create(el);
    });
  }

  /**
   * Destroy all editor instances
   */
  static destroyAll() {
    PythonEditor.instances.forEach(editor => editor.destroy());
  }
}

/**
 * PromptEditor - A class to manage CodeMirror prompt editor instances with variable autocompletion
 */
class PromptEditor extends BaseEditor {
  /** Map of all editor instances by DOM element */
  static instances = new Map();

  /**
   * Create a new Prompt editor instance
   * @param {HTMLElement} element - DOM element to attach editor to
   */
  constructor(element) {
    super(element, PromptEditor.instances);
  }

  /**
   * Create CodeMirror editor instance
   */
  createEditor() {
    const readOnlyAttr = this.element.hasAttribute('data-readonly');
    const autocompleteVars = JSON.parse(this.element.getAttribute('data-autocomplete-vars') || '[]');

    this.view = new EditorView({
      doc: this.initialValue || "",
      parent: this.element,
      extensions: [
        ...this.getCommonExtensions(readOnlyAttr),
        autocompletion({
          override: [textEditorVarCompletions(autocompleteVars)],
          activateOnTyping: true,
        }),
        highlightAutoCompleteVars(autocompleteVars),
        autocompleteVarTheme(),
        EditorView.lineWrapping,
      ]
    });
  }

  /**
   * Create or update a Prompt editor for an element
   * @param {HTMLElement} element - DOM element to attach editor to
   * @returns {PromptEditor} The editor instance
   */
  static create(element) {
    // Clean up existing instance if present
    if (PromptEditor.instances.has(element)) {
      PromptEditor.instances.get(element).destroy();
    }

    return new PromptEditor(element);
  }

  /**
   * Initialize all editors matching a selector
   * @param {string} selector - CSS selector to find editor elements
   */
  static initAll(selector = '.prompt-editor') {
    Array.from(document.querySelectorAll(selector)).forEach(el => {
      PromptEditor.create(el);
    });
  }

  /**
   * Destroy all editor instances
   */
  static destroyAll() {
    PromptEditor.instances.forEach(editor => editor.destroy());
  }
}

// Initialize editors when the DOM is loaded
export const initJsonEditors = (parent) => {
  JsonEditor.initAll(parent);
};

// Create a single editor instance
export const createJsonEditor = (element) => {
  return JsonEditor.create(element);
};

// Cleanup all editors
export const destroyAllEditors = () => {
  JsonEditor.destroyAll();
};

// Initialize Python editors
export const initPythonEditors = () => {
  PythonEditor.initAll();
};

// Create a Python editor instance
export const createPythonEditor = (element) => {
  return PythonEditor.create(element);
};

// Initialize Prompt editors
export const initPromptEditors = () => {
  PromptEditor.initAll();
};

// Create a Prompt editor instance
export const createPromptEditor = (element) => {
  return PromptEditor.create(element);
};

// Global HTMX handler for reinitializing editors
document.addEventListener("htmx:afterSettle", (e) => {
  // Reinitialize JSON editors
  const newJsonEditors = e.detail.target.querySelectorAll('.json-editor');
  if (newJsonEditors.length) {
    Array.from(newJsonEditors).forEach(el => {
      createJsonEditor(el);
    });
  }

  // Reinitialize Python editors
  const newPythonEditors = e.detail.target.querySelectorAll('.python-editor');
  if (newPythonEditors.length) {
    Array.from(newPythonEditors).forEach(el => {
      createPythonEditor(el);
    });
  }

  // Reinitialize Prompt editors
  const newPromptEditors = e.detail.target.querySelectorAll('.prompt-editor');
  if (newPromptEditors.length) {
    Array.from(newPromptEditors).forEach(el => {
      createPromptEditor(el);
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
  // Initialize diff views
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

  // Initialize Python editors
  PythonEditor.initAll();

  // Initialize Prompt editors
  PromptEditor.initAll();
});

// Temporary global for templates not yet migrated
// Used by: templates/evaluations/*.html, templates/participants/single_participant_home.html
// TODO: Migrate templates to use direct imports
window.SiteJS = window.SiteJS || {};
window.SiteJS.editors = {
  initJsonEditors,
  createJsonEditor,
  destroyAllEditors,
  initPythonEditors,
  createPythonEditor,
  initPromptEditors,
  createPromptEditor,
  createDiffView
};
