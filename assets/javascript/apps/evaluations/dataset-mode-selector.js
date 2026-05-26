// Shared session management functionality for dataset forms
const sessionManagement = {
  syncFromHiddenInputs(component) {
    // Use Alpine's $refs to access form elements
    const sessionIdsInput = component.$refs.sessionIds;

    if (sessionIdsInput?.value) {
      sessionIdsInput.value.split(',')
        .filter(id => id.trim())
        .forEach(id => component.selectedSessionIds.add(id));
    }
  },

  cleanupRemovedFromAvailable(component) {
    // Drop selected IDs that are no longer in the available set (e.g. after a filter change).
    component.selectedSessionIds = new Set(
      [...component.selectedSessionIds].filter(id => component.allSessionIds.has(id))
    );
    this.syncHiddenInputs(component);
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

    this.syncHiddenInputs(component);
    this.updateHeaderCheckboxes(component);
  },

  clearAllSelections(component) {
    component.selectedSessionIds = new Set();
    this.syncHiddenInputs(component);
    document.querySelectorAll('.session-checkbox:checked')
      .forEach(cb => cb.checked = false);
  },

  syncHiddenInputs(component) {
    const sessionIdsInput = component.$refs.sessionIds;
    if (sessionIdsInput) {
      sessionIdsInput.value = Array.from(component.selectedSessionIds).join(',');
    }
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

    this.updateHeaderCheckboxes(component);
  },

  toggleSelectedSessions(component, val) {
    const toggleInput = document.querySelector('thead .session-checkbox');
    if (toggleInput.checked || val) {
      component.selectedSessionIds = new Set([...component.allSessionIds]);
    } else {
      component.selectedSessionIds = new Set();
    }

    this.syncHiddenInputs(component);
    this.restoreCheckboxStates(component);
  },

  updateHeaderCheckboxes(component) {
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
};

window.datasetModeSelector = function(options = {}) {
  return {
    loaded: false,
    mode: options.defaultMode || 'clone',
    evaluationMode: options.evaluationMode || 'message',
    selectedSessionIds: new Set(),
    allSessionIds: new Set(),
    sessionIdsFetchUrl: options.sessionIdsFetchUrl || '',
    sessionIdsIsLoading: false,
    errorMessages: [],

    updateModeRadioVisibility() {
      ['manual', 'csv'].forEach(modeValue => {
        const radioInput = document.querySelector(`input[name="mode"][value="${modeValue}"]`);
        if (radioInput) {
          const container = radioInput.closest('li') || radioInput.parentElement;
          if (container) container.style.display = this.evaluationMode === 'session' ? 'none' : '';
        }
      });
    },

    init() {
      // Watch for mode changes using Alpine's $watch
      this.$watch('mode', () => {
        this.errorMessages = [];
      });

      this.$nextTick(() => {
        sessionManagement.syncFromHiddenInputs(this);
      });

      // Form validation for clone mode
      if (this.$refs.cloneForm) {
        this.$refs.cloneForm.addEventListener('submit', (e) => this.validateForm(e));
      }

      window.addEventListener('dataset-mode:table-update', () => this.onSessionsTableUpdate());
      window.addEventListener('filter:change', () => this.loadSessionIds());
      window.addEventListener('dataset-mode:session-ids-loaded', () => this.clearAllSelections());

      this.$nextTick(() => this.updateModeRadioVisibility());

      this.loaded = true;
    },

    validateForm(e) {
      this.errorMessages = [];

      if (this.mode === 'clone') {
        if (this.selectedSessionIds.size === 0) {
          e.preventDefault();
          this.errorMessages.push('Please select at least one session to clone messages from.');
          window.scrollTo({top: 0, behavior: 'smooth'});
        }
      }
    },

    // Delegate to shared module
    updateSelectedSessions() {
      sessionManagement.updateSelectedSessions(this);
      this.errorMessages = [];
    },
    clearAllSelections() {
      sessionManagement.clearAllSelections(this);
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

      // Merge any params baked into sessionIdsFetchUrl (e.g. dataset_id) with the
      // current filter params from window.location.search. Naively concatenating
      // produces a double-'?' URL when both sides have query strings.
      const fetchUrl = new URL(this.sessionIdsFetchUrl, window.location.origin);
      new URLSearchParams(window.location.search).forEach((value, key) => {
        fetchUrl.searchParams.append(key, value);
      });

      return fetch(fetchUrl.toString(), {
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
    onSessionsTableUpdate() {
      sessionManagement.updateHeaderCheckboxes(this);
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
        }
      }
      window.dispatchEvent(new CustomEvent('dataset-mode:table-update'));
    }, 10);
  };

  document.addEventListener('htmx:afterSettle', restoreCheckboxesForTable);
  document.addEventListener('htmx:afterRequest', restoreCheckboxesForTable);
});
