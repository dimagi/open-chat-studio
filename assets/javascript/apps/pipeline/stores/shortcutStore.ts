import { create } from "zustand";
import {shortcutsStoreType} from "../types/shortcutStore";
export const IS_MAC = navigator.userAgent.toUpperCase().includes("MAC");

const defaultShortcuts = [
  {
    name: "Delete",
    shortcut: "Backspace",
  },
];

export const useShortcutsStore = create<shortcutsStoreType>((set, get) => {
  return ({
    shortcuts: defaultShortcuts,
    setShortcuts: (newShortcuts) => {
      set({shortcuts: newShortcuts});
    },
    delete: "backspace",
    updateUniqueShortcut: (name, combination) => {
      set({
        [name]: combination,
      });
    },
    getShortcutsFromStorage: () => {
      if (localStorage.getItem("langflow-shortcuts")) {
        const savedShortcuts = localStorage.getItem("langflow-shortcuts");
        const savedArr = JSON.parse(savedShortcuts!);
        savedArr.forEach(({name, shortcut}) => {
          const shortcutName = name.split(" ")[0].toLowerCase();
          set({
            [shortcutName]: shortcut,
          });
        });
        get().setShortcuts(JSON.parse(savedShortcuts!));
      }
    },
  });
});

useShortcutsStore.getState().getShortcutsFromStorage();
