import React, {useEffect} from "react";
import {ErrorBoundary} from "react-error-boundary";
import {apiClient} from "./api/api";
import Page from "./Page";
import usePipelineStore from "./stores/pipelineStore";

type WidgetElement = HTMLElement & { pageContext?: Record<string, unknown> };

function getWidget(): WidgetElement | null {
  return document.querySelector("open-chat-studio-widget") as WidgetElement | null;
}

function syncPipelineToWidget(nodes: unknown[], edges: unknown[]) {
  const widget = getWidget();
  if (!widget) {
    return;
  }

  const existing = widget.pageContext || {};
  const existingPipelineStructure =
    (existing.pipeline_structure as Record<string, unknown> | undefined) || {};
  widget.pageContext = {
    ...existing,
    pipeline_structure: {
      ...existingPipelineStructure,
      nodes,
      edges,
    },
  };
}

const App = function (props: { team_slug: string, pipelineId: number | undefined}) {
  const isLoading = usePipelineStore((state) => state.isLoading);
  const loadPipeline = usePipelineStore((state) => state.loadPipeline);

  useEffect(() => {
    apiClient.setTeam(props.team_slug);
    if (props.pipelineId) {
      loadPipeline(props.pipelineId);
    }
  }, []);

  useEffect(() => {
    let prevNodes = usePipelineStore.getState().nodes;
    let prevEdges = usePipelineStore.getState().edges;
    return usePipelineStore.subscribe((state) => {
      if (state.nodes !== prevNodes || state.edges !== prevEdges) {
        prevNodes = state.nodes;
        prevEdges = state.edges;
        syncPipelineToWidget(state.nodes, state.edges);
      }
    });
  }, []);

  return isLoading ? (
    <div><span className="loading loading-spinner loading-sm p-3 ml-4"></span></div>
  ) : (
    <ErrorBoundary fallback={<div>Something went wrong</div>}>
      <Page />
    </ErrorBoundary>
  );
};


export default App;
