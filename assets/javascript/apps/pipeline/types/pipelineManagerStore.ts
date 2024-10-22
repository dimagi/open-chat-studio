import {Edge, Node, Viewport} from "reactflow";
import {PipelineType} from "./pipeline";

export type PipelineManagerStoreType = {
  currentPipeline: PipelineType | undefined;
  currentPipelineId: number | undefined;
  dirty: boolean | undefined;
  isSaving: boolean;
  loadPipeline: (pipelineId: number) => void;
  isLoading: boolean;
  setIsLoading: (isLoading: boolean) => void;
  savePipeline: (pipelne: PipelineType, isAutoSave?: boolean) => Promise<void>;
  autoSaveCurrentPipline: (
    nodes: Node[],
    edges: Edge[],
    viewport: Viewport
  ) => void;
};
