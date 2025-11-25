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

// Backward compatibility shim (TODO: Remove after Phase 6)
(window as any).SiteJS = (window as any).SiteJS || {};
(window as any).SiteJS.pipeline = { renderPipeline };
