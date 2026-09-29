/**
 * Alpine.js component for the migration card's chatbot allowlist picker.
 *
 * Usage:
 *   x-data="chatbotAllowlist({ scope: 'all', initialSelection: [1, 2], isMigrating: false })"
 *
 * Expects a `<select x-ref="allowlist" multiple>` inside the component.
 */
window.chatbotAllowlist = function ({scope = 'all', initialSelection = [], isMigrating = false} = {}) {
  // Kept outside the returned object so Alpine does not wrap the TomSelect instance in a proxy.
  let picker = null;

  return {
    scope,
    initialSelection,
    selection: [],

    init() {
      picker = new window.TomSelect(this.$refs.allowlist, {
        plugins: ['remove_button'],
        maxItems: null,
        searchField: ['text'],
        hideSelected: true,
        closeAfterSelect: true,
        onChange: (values) => {
          this.selection = (values || []).map(Number);
        },
      });
      this.selection = picker.getValue().map(Number);
      this.syncPickerState();
      this.$watch('scope', () => this.syncPickerState());
    },

    destroy() {
      picker?.destroy();
      picker = null;
    },

    syncPickerState() {
      if (this.scope === 'all') {
        picker.disable();
      } else {
        picker.enable();
      }
    },

    selectAll() {
      picker.setValue(Object.keys(picker.options));
    },

    clearAll() {
      picker.clear();
    },

    get changedMidMigration() {
      const sorted = (ids) => JSON.stringify([...ids].sort());
      return isMigrating && sorted(this.selection) !== sorted(this.initialSelection);
    },
  };
};
