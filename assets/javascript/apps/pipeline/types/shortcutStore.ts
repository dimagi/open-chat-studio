export type shortcutsStoreType = {
  updateUniqueShortcut: (name: string, combination: string) => void;
  delete: string;
  shortcuts: Array<{
    name: string;
    shortcut: string;
  }>;
  setShortcuts: (
    newShortcuts: Array<{ name: string; shortcut: string }>,
  ) => void;
  getShortcutsFromStorage: () => void;
};
