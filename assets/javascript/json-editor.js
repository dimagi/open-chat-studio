import { basicSetup } from "codemirror"
import { EditorView } from "@codemirror/view"
import { linter, lintGutter, diagnosticCount } from "@codemirror/lint"
import { json, jsonParseLinter } from "@codemirror/lang-json"
import { Compartment, EditorState } from "@codemirror/state"

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
    this.errorContainer.className = 'json-editor-error text-error text-sm mt-1';
    this.element.parentNode.insertBefore(this.errorContainer, this.element.nextSibling);
  }
  
  /**
   * Set up HTMX event listeners
   */
  setupEventListeners() {
    this.element.addEventListener('htmx:beforeRequest', () => {
      if (this.view) {
        this.view.dispatch({
          effects: this.readOnly.reconfigure(EditorState.readOnly.of(true))
        });
      }
    });

    this.element.addEventListener('htmx:afterRequest', () => {
      if (this.view) {
        this.view.dispatch({
          effects: this.readOnly.reconfigure(EditorState.readOnly.of(false))
        });
      }
    });
  }
  
  /**
   * Create CodeMirror editor instance
   */
  createEditor() {
    this.view = new EditorView({
      doc: this.initialValue || "",
      parent: this.element,
      extensions: [
        basicSetup,
        json(),
        linter(jsonParseLinter(), { delay: 250 }),
        lintGutter(),
        this.readOnly.of(EditorState.readOnly.of(false)),
        EditorView.updateListener.of(this.handleEditorUpdate.bind(this))
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
