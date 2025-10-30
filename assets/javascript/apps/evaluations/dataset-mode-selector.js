// Shared session management functionality for dataset forms
const sessionManagement = {
  initializeSelections(component) {
    // Use Alpine's $refs to access form elements
    const sessionIdsInput = component.$refs.sessionIds;
    const filteredSessionIdsInput = component.$refs.filteredSessionIds;

    if (sessionIdsInput?.value) {
      sessionIdsInput.value.split(',')
        .filter(id => id.trim())
        .forEach(id => component.selectedSessionIds.add(id));
    }

    if (filteredSessionIdsInput?.value) {
      filteredSessionIdsInput.value.split(',')
        .filter(id => id.trim())
        .forEach(id => component.filteredSessionIds.add(id));
    }
  },

  updateSelectedSessions(component) {
    const checkboxes = document.querySelectorAll('tbody.session-checkbox:checked');
    const currentPageSelections = Array.from(checkboxes).map(cb => cb.value);
    const allCurrentPageCheckboxes = document.querySelectorAll('tbody.session-checkbox');
    const currentPageSessionIds = Array.from(allCurrentPageCheckboxes).map(cb => cb.value);

    component.selectedSessionIds = new Set(
      [...component.selectedSessionIds].filter(id => !currentPageSessionIds.includes(id))
    );
    currentPageSelections.forEach(id => component.selectedSessionIds.add(id));

    component.filteredSessionIds = new Set(
      [...component.filteredSessionIds].filter(id => !component.selectedSessionIds.has(id))
    );

    this.updateHiddenFields(component);
    this.uncheckFilterCheckboxes(component);
    this.updateSessionHeaderCheckbox();
    this.updateFilteredSessionHeaderCheckbox();
  },

  updateFilteredSessions(component) {
    const checkboxes = document.querySelectorAll('tbody.filter-checkbox:checked');
    const currentPageSelections = Array.from(checkboxes).map(cb => cb.value);
    const allCurrentPageCheckboxes = document.querySelectorAll('tbody.filter-checkbox');
    const currentPageSessionIds = Array.from(allCurrentPageCheckboxes).map(cb => cb.value);

    component.filteredSessionIds = new Set(
      [...component.filteredSessionIds].filter(id => !currentPageSessionIds.includes(id))
    );
    currentPageSelections.forEach(id => component.filteredSessionIds.add(id));

    component.selectedSessionIds = new Set(
      [...component.selectedSessionIds].filter(id => !component.filteredSessionIds.has(id))
    );

    this.updateHiddenFields(component);
    this.uncheckSessionCheckboxes(component);
    this.updateSessionHeaderCheckbox();
    this.updateFilteredSessionHeaderCheckbox();
  },

  clearAllSelections(component) {
    component.selectedSessionIds = new Set();
    component.filteredSessionIds = new Set();
    this.updateHiddenFields(component);
    document.querySelectorAll('.session-checkbox:checked, .filter-checkbox:checked')
      .forEach(cb => cb.checked = false);
  },

  updateHiddenFields(component) {
    const sessionIdsInput = component.$refs.sessionIds;
    const filteredSessionIdsInput = component.$refs.filteredSessionIds;

    if (sessionIdsInput) {
      sessionIdsInput.value = Array.from(component.selectedSessionIds).join(',');
    }
    if (filteredSessionIdsInput) {
      filteredSessionIdsInput.value = Array.from(component.filteredSessionIds).join(',');
    }
  },

  uncheckFilterCheckboxes(component) {
    component.selectedSessionIds.forEach(id => {
      const checkbox = document.querySelector(`.filter-checkbox[value="${id}"]`);
      if (checkbox) checkbox.checked = false;
    });
  },

  uncheckSessionCheckboxes(component) {
    component.filteredSessionIds.forEach(id => {
      const checkbox = document.querySelector(`.session-checkbox[value="${id}"]`);
      if (checkbox) checkbox.checked = false;
    });
  },

  restoreCheckboxStates(component) {
    const sessionIdsInput = component.$refs.sessionIds;
    if (!sessionIdsInput) return;

    const selectedIds = sessionIdsInput.value ?
      sessionIdsInput.value.split(',').filter(id => id.trim()) : [];

    document.querySelectorAll('.session-checkbox').forEach(cb => cb.checked = false);
    selectedIds.forEach(sessionId => {
      const checkbox = document.querySelector(`.session-checkbox[value="${sessionId}"]`);
      if (checkbox) checkbox.checked = true;
    });
  },

  restoreFilteredCheckboxStates(component) {
    const filteredSessionIdsInput = component.$refs.filteredSessionIds;
    if (!filteredSessionIdsInput) return;

    const filteredIds = filteredSessionIdsInput.value ?
      filteredSessionIdsInput.value.split(',').filter(id => id.trim()) : [];

    document.querySelectorAll('.filter-checkbox').forEach(cb => cb.checked = false);
    filteredIds.forEach(sessionId => {
      const checkbox = document.querySelector(`.filter-checkbox[value="${sessionId}"]`);
      if (checkbox) checkbox.checked = true;
    });
  },
};

window.datasetModeSelector = function(options = {}) {
  return {
    loaded: false,
    mode: options.defaultMode || 'clone',
    selectedSessionIds: new Set(),
    filteredSessionIds: new Set(),
    errorMessages: [],

    init() {
      // Watch for mode changes using Alpine's $watch
      this.$watch('mode', () => {
        this.errorMessages = [];
      });

      this.$nextTick(() => {
        sessionManagement.initializeSelections(this);
      });

      // Form validation for clone mode
      if (this.$refs.cloneForm) {
        this.$refs.cloneForm.addEventListener('submit', (e) => this.validateForm(e));
      }

      this.loaded = true;
    },

    validateForm(e) {
      this.errorMessages = [];

      if (this.mode === 'clone') {
        if (this.selectedSessionIds.size + this.filteredSessionIds.size === 0) {
          e.preventDefault();
          this.errorMessages.push('Please select at least one session to clone messages from.');
          window.scrollTo({ top: 0, behavior: 'smooth' });
          return;
        }

        const intersection = [...this.selectedSessionIds]
          .filter(id => this.filteredSessionIds.has(id));
        if (intersection.length > 0) {
          e.preventDefault();
          this.errorMessages.push(
            'A session cannot be selected in both "All Messages" and "Filtered Messages".'
          );
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }
      }
    },

    // Delegate to shared module
    updateSelectedSessions() {
      sessionManagement.updateSelectedSessions(this);
      this.errorMessages = [];
    },
    updateFilteredSessions() {
      sessionManagement.updateFilteredSessions(this);
    },
    clearAllSelections() {
      sessionManagement.clearAllSelections(this);
    },
    restoreFilteredCheckboxStates() {
      sessionManagement.restoreFilteredCheckboxStates(this);
    },
    restoreCheckboxStates() {
      sessionManagement.restoreCheckboxStates(this);
    }
  };
};

document.addEventListener('DOMContentLoaded', () => {
  const restoreCheckboxesForTable = (event) => {
    if (event.target.id !== 'sessions-table') return;
    setTimeout(() => {
      const alpineEl = document.querySelector('[x-data*="datasetModeSelector"]');
      if (alpineEl) {
        const component = window.Alpine.$data(alpineEl);
        if (component) {
          sessionManagement.restoreCheckboxStates(component);
          sessionManagement.restoreFilteredCheckboxStates(component);
        }
      }
    }, 10);
  };

  document.addEventListener('htmx:afterSettle', restoreCheckboxesForTable);
  document.addEventListener('htmx:afterRequest', restoreCheckboxesForTable);
});
