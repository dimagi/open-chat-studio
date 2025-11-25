'use strict';
import React from "react";
import {createRoot} from "react-dom/client";
import App from "./pipeline/App";

declare global {
  const alertify: any;
}

export function renderPipeline(containerId: string, team_slug: string, pipelineId: number | undefined) {
  const root = document.querySelector(containerId)!;
  createRoot(root).render(<App team_slug={team_slug} pipelineId={pipelineId} />);
}

// Temporary global for templates not yet migrated
// Used by: templates/pipelines/pipeline_builder.html
// TODO: Migrate template to use direct imports
(window as any).SiteJS = (window as any).SiteJS || {};
(window as any).SiteJS.pipeline = { renderPipeline };
