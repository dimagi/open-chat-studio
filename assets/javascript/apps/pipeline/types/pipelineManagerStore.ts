import {Edge, Node} from "reactflow";
import {PipelineType} from "./pipeline";

export type PipelineManagerStoreType = {
  currentPipeline: PipelineType | undefined;
  currentPipelineId: number | undefined;
  dirty: boolean | undefined;
  isSaving: boolean;
  loadPipeline: (pipelineId: number) => void;
  isLoading: boolean;
  setIsLoading: (isLoading: boolean) => void;
  updatePipelineName: (name: string) => void;
  savePipeline: (pipelne: PipelineType, isAutoSave?: boolean) => Promise<void>;
  autoSaveCurrentPipline: (
    nodes: Node[],
    edges: Edge[],
  ) => void;
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
