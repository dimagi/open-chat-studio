'use strict';
import React from "react";
import {createRoot} from "react-dom/client";
import PipelineApplication from "./pipeline/App";


export function renderPipeline(containerId: string) {
  const domContainer = document.querySelector(containerId)!;
  createRoot(domContainer).render(<PipelineApplication />);
}
