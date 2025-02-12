import React, {useEffect} from "react";
import {ErrorBoundary} from "react-error-boundary";
import {apiClient} from "./api/api";
import Page from "./Page";
import usePipelineStore from "./stores/pipelineStore";

const App = function (props: { team_slug: string, pipelineId: number | undefined}) {
  const isLoading = usePipelineStore((state) => state.isLoading);
  const loadPipeline = usePipelineStore((state) => state.loadPipeline);

  useEffect(() => {
    apiClient.setTeam(props.team_slug);
    if (props.pipelineId) {
      loadPipeline(props.pipelineId);
    }
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
