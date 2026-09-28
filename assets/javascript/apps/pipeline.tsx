'use strict';
import React from "react";
import {createRoot} from "react-dom/client";
import App from "./pipeline/App";

declare global {
  // `alertifyjs` ships no type declarations. Only the notifier methods reached for from the
  // pipeline app are declared; widen this as more of the library gets used.
  const alertify: {
    error: (message: string) => void;
    success: (message: string) => void;
    warning: (message: string) => void;
  };
}

export function renderPipeline(containerId: string, team_slug: string, pipelineId: number | undefined) {
  const root = document.querySelector(containerId)!;
  createRoot(root).render(<App team_slug={team_slug} pipelineId={pipelineId} />);
}
