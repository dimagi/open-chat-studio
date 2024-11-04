import { create } from 'zustand'

export type FieldError = {
    name: string;
    errorMsg: string
  }
  
  export type NodeError = {
    nodeId: string;
    fields: FieldError[]
  }

type ErrorStoreType = {
    errors: NodeError[],
    setFieldError: (nodeId: string, fieldName: string, errorMsg: string) => void;
    fieldError: (nodeId: string, fieldName: string) => string | undefined;
    clearFieldErrors: (nodeId: string, fieldName: string) => undefined;
}

const useNodeErrorStore = create<ErrorStoreType>((set, get) => ({
    errors: [],
    setFieldError: (nodeId: string, fieldName: string, errorMsg: string) => {
        const currentErrors = get().errors;
        const updatedErrors = {
        ...currentErrors, [nodeId]: {
            ...currentErrors[nodeId], [fieldName]: errorMsg
        }
        }
        set({errors: updatedErrors});
    },
    fieldError: (nodeId: string, fieldName: string) => {
        const nodeError = get().errors[nodeId];
        return nodeError ? nodeError[fieldName] : "";
    },
    clearFieldErrors: (nodeId: string, fieldName: string) => {
        get().setFieldError(nodeId, fieldName, "");
    }
}))

export default useNodeErrorStore;
