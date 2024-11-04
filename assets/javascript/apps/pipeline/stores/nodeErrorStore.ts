import { create } from 'zustand'


type ErrorStoreType = {
    errors: {[nodeId: string]: {[name: string]: string}},
    setFieldError: (nodeId: string, fieldName: string, errorMsg: string) => void;
    fieldError: (nodeId: string, fieldName: string) => string | undefined;
    clearFieldErrors: (nodeId: string, fieldName: string) => void;
    hasErrors: (nodeId: string) => boolean;
}

const useNodeErrorStore = create<ErrorStoreType>((set, get) => ({
    errors: {},
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
        if (nodeId in get().errors) {
            delete get().errors[nodeId][fieldName];
            // Remove `nodeId` when there's not more field errors
            if (Object.keys(get().errors[nodeId]).length === 0) {
                delete get().errors[nodeId];
            }
        }
    },
    hasErrors: (nodeId: string): boolean =>  {
        return nodeId in get().errors;
    }
}))

export default useNodeErrorStore;
