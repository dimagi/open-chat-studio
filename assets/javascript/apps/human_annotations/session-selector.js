/**
 * Alpine.js component for selecting sessions to add to an annotation queue.
 *
 * Supports three modes:
 * - "selected": hand-picked sessions via checkboxes (default)
 * - "all_matching": all sessions matching current filters
 * - "sample": random percentage of sessions matching current filters
 *
 * Usage:
 *   x-data="annotationQueueSessionSelector({ sessionIdsFetchUrl: '...' })"
 */
window.annotationQueueSessionSelector = function (options = {}) {
  return {
    selectedSessionIds: new Set(),
    allSessionIds: new Set(),
    totalCount: 0,
    sessionIdsFetchUrl: options.sessionIdsFetchUrl || '',
    sessionIdsString: '',
    errorMessages: [],
    sessionIdsIsLoading: false,

    // Scope mode: 'selected', 'all_matching', or 'sample'
    mode: 'selected',
    samplePercent: 20,
    showConfirmModal: false,
    filterParams: [],

    init() {
      this._syncFilterParams();
      document.addEventListener('htmx:afterSettle', (e) => {
        if (e.target.id === 'sessions-table') this.restoreCheckboxStates();
      });
      window.addEventListener('filter:change', () => {
        this.clearAllSelections();
        this.loadSessionIds();
        this._syncFilterParams();
      });
      this.loadSessionIds();
    },

    _syncFilterParams() {
      this.filterParams = Array.from(new URLSearchParams(window.location.search).entries()).map(
        ([k, v]) => ({ name: k, value: v }),
      );
    },

    get pillText() {
      if (this.mode === 'selected') {
        return `${this.selectedSessionIds.size} selected`;
      }
      if (this.mode === 'all_matching') {
        return `All ${this.totalCount} sessions`;
      }
      // sample
      const estimated = this.estimatedSampleCount;
      return `~${estimated} sessions (${this.samplePercent}%)`;
    },

    get pillClass() {
      if (this.mode === 'selected' && this.selectedSessionIds.size === 0) {
        return 'badge-warning';
      }
      return 'badge-primary';
    },

    get buttonLabel() {
      if (this.mode === 'selected') {
        const n = this.selectedSessionIds.size;
        return `Add ${n} to queue`;
      }
      if (this.mode === 'all_matching') {
        return `Add ${this.totalCount} to queue`;
      }
      return `Add ~${this.estimatedSampleCount} to queue`;
    },

    get isSubmitDisabled() {
      return this.mode === 'selected' && this.selectedSessionIds.size === 0;
    },

    get estimatedSampleCount() {
      if (this.totalCount === 0) return 0;
      return Math.max(1, Math.round((this.totalCount * this.samplePercent) / 100));
    },

    get needsConfirmation() {
      if (this.mode === 'all_matching') return true;
      if (this.mode === 'sample' && this.estimatedSampleCount > 200) return true;
      return false;
    },

    get confirmMessage() {
      if (this.mode === 'all_matching') {
        return `All sessions matching your current filters will be added. This cannot be undone from this screen.`;
      }
      return `A random sample of ${this.samplePercent}% (~${this.estimatedSampleCount} sessions) will be added. This cannot be undone from this screen.`;
    },

    get confirmTitle() {
      const queueName = document.querySelector('[data-queue-name]')?.dataset.queueName || 'queue';
      if (this.mode === 'all_matching') {
        return `Add ${this.totalCount} sessions to "${queueName}"?`;
      }
      return `Add ~${this.estimatedSampleCount} sessions to "${queueName}"?`;
    },

    setMode(newMode) {
      this.mode = newMode;
      this.errorMessages = [];
    },

    clampSamplePercent() {
      let val = parseInt(this.samplePercent, 10);
      if (isNaN(val) || val < 1) val = 1;
      if (val > 100) val = 100;
      this.samplePercent = val;
    },

    async loadSessionIds() {
      if (this.sessionIdsIsLoading) return;
      this.sessionIdsIsLoading = true;
      try {
        const res = await fetch(this.sessionIdsFetchUrl + window.location.search, {
          credentials: 'same-origin',
          headers: {
            'X-CSRFToken': window.SiteJS.app.Cookies.get('csrftoken'),
            Accept: 'application/json',
          },
        });
        const data = await res.json();
        this.allSessionIds = new Set(data.ids.map(String));
        this.totalCount = data.total;
      } catch (_e) {
        this.errorMessages = ['Failed to load sessions. Please refresh the page.'];
      } finally {
        this.sessionIdsIsLoading = false;
      }
    },

    updateSelectedSessions() {
      const allCheckboxes = document.querySelectorAll('tbody .session-checkbox');
      const currentPageIds = Array.from(allCheckboxes).map((cb) => cb.value);
      const checkedIds = Array.from(
        document.querySelectorAll('tbody .session-checkbox:checked'),
      ).map((cb) => cb.value);

      this.selectedSessionIds = new Set(
        [...this.selectedSessionIds].filter((id) => !currentPageIds.includes(id)),
      );
      checkedIds.forEach((id) => this.selectedSessionIds.add(id));

      this.syncHiddenField();
      this.updateHeaderCheckbox();
      this.errorMessages = [];
    },

    toggleSelectedSessions() {
      const header = document.querySelector('thead .session-checkbox');
      if (header && header.checked) {
        this.allSessionIds.forEach((id) => this.selectedSessionIds.add(String(id)));
      } else {
        document.querySelectorAll('tbody .session-checkbox').forEach((cb) => {
          this.selectedSessionIds.delete(cb.value);
        });
      }
      this.syncHiddenField();
      this.restoreCheckboxStates();
    },

    clearAllSelections() {
      this.selectedSessionIds = new Set();
      this.syncHiddenField();
      document.querySelectorAll('.session-checkbox:checked').forEach((cb) => (cb.checked = false));
      this.updateHeaderCheckbox();
    },

    restoreCheckboxStates() {
      document.querySelectorAll('tbody .session-checkbox').forEach((cb) => {
        cb.checked = this.selectedSessionIds.has(cb.value);
      });
      this.updateHeaderCheckbox();
    },

    updateHeaderCheckbox() {
      const header = document.querySelector('thead .session-checkbox');
      if (!header) return;
      const pageIds = Array.from(document.querySelectorAll('tbody .session-checkbox')).map(
        (cb) => cb.value,
      );
      header.checked =
        pageIds.length > 0 && pageIds.every((id) => this.selectedSessionIds.has(id));
    },

    syncHiddenField() {
      this.sessionIdsString = Array.from(this.selectedSessionIds).join(',');
    },

    handleSubmit(e) {
      this.errorMessages = [];

      if (this.mode === 'selected' && this.selectedSessionIds.size === 0) {
        e.preventDefault();
        this.errorMessages = ['Select at least one session above.'];
        window.scrollTo({ top: 0, behavior: 'smooth' });
        return;
      }

      if (this.needsConfirmation) {
        e.preventDefault();
        this.showConfirmModal = true;
        return;
      }
    },

    confirmSubmit() {
      this.showConfirmModal = false;
      document.getElementById('add-sessions-form').submit();
    },

    cancelConfirm() {
      this.showConfirmModal = false;
    },
  };
};
