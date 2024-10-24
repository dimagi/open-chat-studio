import { create } from 'zustand'
import {NodeProps} from "reactflow";
import {NodeData} from "../types/nodeParams";

type EditorStoreType = {
  isOpen: boolean;
  currentNode: NodeProps<NodeData> | null;
  openEditorForNode: (node: NodeProps<NodeData>) => void;
  closeEditor: () => void;
}

const useEditorStore = create<EditorStoreType>((set) => ({
  currentNode: null,
  isOpen: false,
  openEditorForNode: (node: NodeProps<NodeData>) => {
    set({currentNode: node, isOpen: true});
  },
  closeEditor: () => {
    set({currentNode: null, isOpen: false});
  }
}))

export default useEditorStore;
