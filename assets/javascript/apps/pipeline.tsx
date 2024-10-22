'use strict';
import React from "react";
import {createRoot} from "react-dom/client";
import App from "./pipeline/App";
import { NodeInputTypes } from "./pipeline/types/nodeInputTypes";

declare global {
  const alertify: any;
}

export function renderPipeline(containerId: string, team_slug: string, pipelineId: number | undefined, inputTypes: NodeInputTypes[]) {
  const root = document.querySelector(containerId)!;
  createRoot(root).render(<App team_slug={team_slug} pipelineId={pipelineId} inputTypes={inputTypes} />);
}
