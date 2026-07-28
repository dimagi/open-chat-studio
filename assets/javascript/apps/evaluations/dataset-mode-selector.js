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

  // Header checkbox selects the current page only. Selecting every session matching the
  // filters is the 'all_matching' scope instead, which never enumerates ids client-side.
  toggleSelectedSessions(component, val) {
    const toggleInput = document.querySelector('thead .session-checkbox');
    const pageIds = Array.from(document.querySelectorAll('tbody .session-checkbox')).map(cb => cb.value);
    if (toggleInput?.checked || val) {
      pageIds.forEach(id => component.selectedSessionIds.add(id));
    } else {
      pageIds.forEach(id => component.selectedSessionIds.delete(id));
    }

    this.syncHiddenInputs(component);
    this.restoreCheckboxStates(component);
  },

  updateHeaderCheckboxes(component) {
    const selectedSessionIds = component.selectedSessionIds;
    const toggleInput = document.querySelector('thead .session-checkbox');
    if (!toggleInput) {
      return; // page load
    }
    const pageIds = Array.from(document.querySelectorAll('tbody .session-checkbox')).map(cb => cb.value);
    toggleInput.checked = pageIds.length > 0 && pageIds.every(id => selectedSessionIds.has(id));
  },
};

window.datasetModeSelector = function(options = {}) {
  return {
    loaded: false,
    mode: options.defaultMode || 'clone',
    evaluationMode: options.evaluationMode || 'message',
    selectedSessionIds: new Set(),
    // 'selected' (hand-picked rows) or 'all_matching' (resolved server-side from the filters).
    sessionScope: 'selected',
    // The active filters, mirrored into the POST body as hidden inputs.
    filterParams: [],
    totalCount: 0,
    sessionCountUrl: options.sessionCountUrl || '',
    // Message-level datasets cap 'all_matching' (see MESSAGE_MODE_ALL_MATCHING_LIMIT); 0 disables
    // the client-side warning and leaves the server as the only check.
    messageModeLimit: options.messageModeLimit || 0,
    // Monotonic request token rather than an in-flight boolean: in 'all_matching' scope the count
    // IS what the user is told will be cloned, so a request must never be dropped just because an
    // older one is still open — the last request issued has to be the one that sets totalCount.
    countRequestId: 0,
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
        // The server picks the initial scope (arriving with filters pre-selects 'all_matching'),
        // so read it from the hidden input rather than overwriting it.
        if (this.$refs.sessionScope?.value) {
          this.sessionScope = this.$refs.sessionScope.value;
        }
      });

      // Form validation for clone mode
      if (this.$refs.cloneForm) {
        this.$refs.cloneForm.addEventListener('submit', (e) => this.validateForm(e));
      }

      window.addEventListener('dataset-mode:table-update', () => this.onSessionsTableUpdate());
      window.addEventListener('filter:change', () => {
        this.clearAllSelections();
        this.loadCount();
        this.syncFilterParams();
      });

      this.$nextTick(() => this.updateModeRadioVisibility());

      this.syncFilterParams();
      this.loadCount();
      this.loaded = true;
    },

    // Only f_/op_ params are mirrored: they are the reserved filter prefixes, and copying any
    // other query param into this form would collide with its own fields (e.g. ?name=...).
    syncFilterParams() {
      this.filterParams = Array.from(new URLSearchParams(window.location.search).entries())
        .filter(([name]) => name.startsWith('f_') || name.startsWith('op_'))
        .map(([name, value]) => ({name, value}));
    },

    setSessionScope(scope) {
      this.sessionScope = scope;
      if (this.$refs.sessionScope) {
        this.$refs.sessionScope.value = scope;
      }
      this.errorMessages = [];
    },

    // A method, not a getter: datasetModeSelectorBuilder spreads this object, and a spread
    // invokes getters once at build time instead of carrying them over.
    cloneCount() {
      return this.sessionScope === 'all_matching' ? this.totalCount : this.selectedSessionIds.size;
    },

    // Warn before submitting a message-level 'all_matching' clone the server will reject. Session
    // level is exempt: it ships the filter instead of the ids, so it has no ceiling.
    overMessageModeLimit() {
      return this.messageModeLimit > 0
        && this.sessionScope === 'all_matching'
        && this.evaluationMode !== 'session'
        && this.totalCount > this.messageModeLimit;
    },

    validateForm(e) {
      this.errorMessages = [];

      if (this.mode === 'clone' && this.sessionScope === 'selected' && this.selectedSessionIds.size === 0) {
        e.preventDefault();
        this.errorMessages.push('Please select at least one session to clone messages from.');
        window.scrollTo({top: 0, behavior: 'smooth'});
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
    // How many sessions match the current filters. A count, not the ids: the id list this
    // replaced was ~390 KB of UUIDs at 10k sessions, re-fetched on every filter change.
    loadCount() {
      if (!this.sessionCountUrl) {
        return;
      }
      const requestId = ++this.countRequestId;

      // Merge any params baked into sessionCountUrl (e.g. dataset_id) with the current filter
      // params from window.location.search. Naively concatenating produces a double-'?' URL
      // when both sides have query strings.
      const fetchUrl = new URL(this.sessionCountUrl, window.location.origin);
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
          // Drop a response that a newer filter change has already superseded, otherwise a slow
          // first request can land after a fast second one and show the wrong filter's count.
          if (requestId === this.countRequestId) {
            this.totalCount = data.total;
          }
        })
        .catch(err => {
          console.error('Failed to load session count:' + err);
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
