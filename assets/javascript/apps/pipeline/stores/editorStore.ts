import { create } from 'zustand'
import {NodeProps} from "reactflow";
import {NodeData} from "../types/nodeParams";

type EditorStoreType = {
  currentNode: NodeProps<NodeData> | null;
  openEditorForNode: (node: NodeProps<NodeData>) => void;
  closeEditor: () => void;
}

const useEditorStore = create<EditorStoreType>((set) => ({
  currentNode: null,
  openEditorForNode: (node: NodeProps<NodeData>) => {
    set({currentNode: node});
  },
  closeEditor: () => {
    set({currentNode: null});
  }
}))

export default useEditorStore;
