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
    const checkboxes = document.querySelectorAll('tbody .session-checkbox:checked');
    const currentPageSelections = Array.from(checkboxes).map(cb => cb.value);
    const allCurrentPageCheckboxes = document.querySelectorAll('.session-checkbox');
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
    this.updateSessionHeaderCheckbox(component);
    this.updateFilteredSessionHeaderCheckbox(component);
  },

  updateFilteredSessions(component) {
    const checkboxes = document.querySelectorAll('tbody .filter-checkbox:checked');
    const currentPageSelections = Array.from(checkboxes).map(cb => cb.value);
    const allCurrentPageCheckboxes = document.querySelectorAll('tbody .filter-checkbox');
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
    this.updateSessionHeaderCheckbox(component);
    this.updateFilteredSessionHeaderCheckbox(component);
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

    document.querySelectorAll('tbody .session-checkbox').forEach(cb => cb.checked = false);
    selectedIds.forEach(sessionId => {
      const checkbox = document.querySelector(`.session-checkbox[value="${sessionId}"]`);
      if (checkbox) checkbox.checked = true;
    });

    this.updateSessionHeaderCheckbox(component);
    this.updateFilteredSessionHeaderCheckbox(component);
  },

  restoreFilteredCheckboxStates(component) {
    const filteredSessionIdsInput = component.$refs.filteredSessionIds;
    if (!filteredSessionIdsInput) return;

    const filteredIds = filteredSessionIdsInput.value ?
      filteredSessionIdsInput.value.split(',').filter(id => id.trim()) : [];

    document.querySelectorAll('tbody .filter-checkbox').forEach(cb => cb.checked = false);
    filteredIds.forEach(sessionId => {
      const checkbox = document.querySelector(`.filter-checkbox[value="${sessionId}"]`);
      if (checkbox) checkbox.checked = true;
    });

    this.updateSessionHeaderCheckbox(component);
    this.updateFilteredSessionHeaderCheckbox(component);
  },

  toggleSelectedSessions(component, val) {
    const toggleInput = document.querySelector('thead .session-checkbox');
    if (toggleInput.checked || val) {
      component.selectedSessionIds = new Set([...component.allSessionIds]);
      component.filteredSessionIds = new Set();
    } else {
      component.selectedSessionIds = new Set();
    }

    this.updateHiddenFields(component);
    this.restoreCheckboxStates(component);
    this.uncheckFilterCheckboxes(component);
  },

  toggleFilteredSessions(component, val) {
    const toggleInput = document.querySelector('thead .filter-checkbox');
    if (toggleInput.checked || val) {
      component.filteredSessionIds = new Set([...component.allSessionIds]);
      component.selectedSessionIds = new Set();
    } else {
      component.filteredSessionIds = new Set();
    }

    this.updateHiddenFields(component);
    this.restoreFilteredCheckboxStates(component);
    this.uncheckSessionCheckboxes(component);
  },

  updateSessionHeaderCheckbox(component) {
    const allSessionIds = component.allSessionIds;
    const selectedSessionIds = component.selectedSessionIds;
    const toggleInput = document.querySelector('thead .session-checkbox');
    if (!toggleInput || !allSessionIds.size) {
      return; // page load
    }
    if (selectedSessionIds.size === 0) {
      toggleInput.checked = false;
    } else {
      toggleInput.checked = [...allSessionIds].every(id => selectedSessionIds.has(id));
    }
  },

  updateFilteredSessionHeaderCheckbox(component) {
    const allSessionIds = component.allSessionIds;
    const filteredSessionIds = component.filteredSessionIds;
    const toggleInput = document.querySelector('thead .filter-checkbox');
    if (!toggleInput || !allSessionIds.size) {
      return; // page load
    }
    if (filteredSessionIds.size === 0) {
      toggleInput.checked = false;
    } else {
      toggleInput.checked = [...allSessionIds].every(id => filteredSessionIds.has(id));
    }
  },
};

window.datasetModeSelector = function(options = {}) {
  return {
    loaded: false,
    mode: options.defaultMode || 'clone',
    selectedSessionIds: new Set(),
    filteredSessionIds: new Set(),
    allSessionIds: new Set(),
    sessionIdsFetchUrl: options.sessionIdsFetchUrl || '',
    sessionIdsIsLoading: false,
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

      window.addEventListener('dataset-mode:table-update', () => this.onSessionsTableUpdate());
      window.addEventListener('filter:change', () => this.loadSessionIds());
      window.addEventListener('dataset-mode:session-ids-loaded', () => this.clearAllSelections());

      this.loaded = true;
    },

    validateForm(e) {
      this.errorMessages = [];

      if (this.mode === 'clone') {
        if (this.selectedSessionIds.size + this.filteredSessionIds.size === 0) {
          e.preventDefault();
          this.errorMessages.push('Please select at least one session to clone messages from.');
          window.scrollTo({top: 0, behavior: 'smooth'});
          return;
        }

        const intersection = [...this.selectedSessionIds]
          .filter(id => this.filteredSessionIds.has(id));
        if (intersection.length > 0) {
          e.preventDefault();
          this.errorMessages.push(
            'A session cannot be selected in both "All Messages" and "Filtered Messages".'
          );
          window.scrollTo({top: 0, behavior: 'smooth'});
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
    },
    loadSessionIds() {
      // Loading id from Sessions filtered
      if (this.sessionIdsIsLoading) {
        return;
      }
      this.sessionIdsIsLoading = true;

      return fetch(this.sessionIdsFetchUrl + window.location.search, {
        method: 'GET',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': window.SiteJS.app.Cookies.get('csrftoken'),
          'Accept': 'application/json'
        }
      })
        .then(res => res.json())
        .then(data => {
          this.allSessionIds = new Set(data);
        })
        .catch(err => {
          console.error('Failed to load session ids:' + err);
        })
        .finally(() => {
          this.sessionIdsIsLoading = false;
          window.dispatchEvent(new CustomEvent('dataset-mode:session-ids-loaded'));
        });
    },
    toggleSelectedSessions() {
      sessionManagement.toggleSelectedSessions(this);
    },
    toggleFilteredSessions() {
      sessionManagement.toggleFilteredSessions(this);
    },
    onSessionsTableUpdate() {
      sessionManagement.updateSessionHeaderCheckbox(this);
      sessionManagement.updateFilteredSessionHeaderCheckbox(this);
    },
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
      window.dispatchEvent(new CustomEvent('dataset-mode:table-update'));
    }, 10);
  };

  document.addEventListener('htmx:afterSettle', restoreCheckboxesForTable);
  document.addEventListener('htmx:afterRequest', restoreCheckboxesForTable);
});
