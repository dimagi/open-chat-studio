/**
 * Alpine.js component for selecting sessions to add to an annotation queue.
 *
 * Usage:
 *   x-data="annotationQueueSessionSelector({ sessionIdsFetchUrl: '...' })"
 */
window.annotationQueueSessionSelector = function (options = {}) {
  return {
    selectedSessionIds: new Set(),
    allSessionIds: new Set(),
    sessionIdsFetchUrl: options.sessionIdsFetchUrl || '',
    sessionIdsString: '',
    errorMessages: [],
    sessionIdsIsLoading: false,

    init() {
      // Restore checkbox states after HTMX paginates/sorts the table.
      // Note: 'dataset-mode:table-update' is only dispatched by evaluations-bundle.js,
      // which is not loaded on this page — listen to the underlying HTMX event directly.
      document.addEventListener('htmx:afterSettle', (e) => {
        if (e.target.id === 'sessions-table') this.restoreCheckboxStates();
      });
      window.addEventListener('filter:change', () => this.loadSessionIds());
      this.loadSessionIds();
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
        this.allSessionIds = new Set(data.map(String));
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

      // Remove current page from selected set, then add back only what's checked
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

    validateAndSubmit(e) {
      this.errorMessages = [];
      if (this.selectedSessionIds.size === 0) {
        e.preventDefault();
        this.errorMessages = ['Please select at least one session.'];
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    },
  };
};
