import React, {useEffect} from "react";
import { ErrorBoundary } from "react-error-boundary";
import Pipeline from "./Pipeline";
import usePipelineManagerStore from "./stores/pipelineManagerStore";

const App = function (props: { team_slug: string, pipelineId: number | undefined }) {
  const isLoading = usePipelineManagerStore((state) => state.isLoading);
  const setTeam = usePipelineManagerStore((state) => state.setTeam);
  const loadPipeline = usePipelineManagerStore((state) => state.loadPipeline);

  useEffect(() => {
    setTeam(props.team_slug);
    if (props.pipelineId) {
      loadPipeline(props.pipelineId);
    }
  }, []);

  return isLoading ? (
    <div><span className="loading loading-spinner loading-sm p-3 ml-4"></span></div>
  ) : (
    <ErrorBoundary fallback={<div>Something went wrong</div>}>
      <Pipeline />
    </ErrorBoundary>
  );
};


export default App;
