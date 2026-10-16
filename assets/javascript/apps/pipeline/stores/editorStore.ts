import { create } from 'zustand'
import {NodeProps} from "reactflow";
import {NodeData} from "../types/nodeParams";

type EditorStoreType = {
  currentNode: NodeProps<NodeData> | null;
  /** The param to bring into view in the editor. A new object each time, so repeating a request re-runs it. */
  focusField: {name: string} | null;
  openEditorForNode: (node: NodeProps<NodeData>, field?: string) => void;
  closeEditor: () => void;
}

const useEditorStore = create<EditorStoreType>((set) => ({
  currentNode: null,
  focusField: null,
  openEditorForNode: (node: NodeProps<NodeData>, field?: string) => {
    set({currentNode: node, focusField: field ? {name: field} : null});
  },
  closeEditor: () => {
    set({currentNode: null, focusField: null});
  }
}))

export default useEditorStore;
