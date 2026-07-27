import {PipelineDiffPayload, PipelineType} from "./pipeline";

export type PipelineManagerStoreType = {
  currentPipeline: PipelineType | undefined;
  currentPipelineId: number | undefined;
  dirty: boolean | undefined;
  isSaving: boolean;
  loadPipeline: (pipelineId: number) => void;
  isLoading: boolean;
  setIsLoading: (isLoading: boolean) => void;
  updatePipelineName: (name: string) => void;
  savePipeline: (pipelne: PipelineType) => Promise<void>;
  autoSaveCurrentPipline: () => void;
  // Internal PATCH helper
  _patchPipeline: (diff: PipelineDiffPayload) => Promise<void>;
  // Conflict handling
  conflictDetected: boolean;
  dismissConflict: () => void;
  // Current edit revision for optimistic concurrency
  currentRevision: number;
  // Errors
  errors: ErrorsType;
  nodeHasErrors: (nodeId: string) => boolean;
  getNodeFieldError: (nodeId: string, fieldName: string) => string | undefined;
  edgeHasErrors: (edgeId: string) => boolean;
  getPipelineError: () => string | undefined;
};

export type ErrorsType = {
  node?: {[nodeId: string]: {[field: string]: string}};
  edge?: string[];
  pipeline?: string;
};
