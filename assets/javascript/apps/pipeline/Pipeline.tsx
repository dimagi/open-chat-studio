import React, {useEffect} from 'react';
import ReactFlow, {Background, BackgroundVariant, Controls, FitViewOptions, NodeTypes} from 'reactflow';

import {MyCustomNode} from './CustomNode';

import 'reactflow/dist/style.css';
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import usePipelineStore from "./stores/pipelineStore";

const fitViewOptions: FitViewOptions = {
  padding: 0.2,
};

const nodeTypes: NodeTypes = {
  custom: MyCustomNode,
};

export default function Pipeline() {
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);
  const onNodesChange = usePipelineStore((state) => state.onNodesChange);
  const onEdgesChange = usePipelineStore((state) => state.onEdgesChange);
  const onConnect = usePipelineStore((state) => state.onConnect);
  const resetFlow = usePipelineStore((state) => state.resetFlow);
  const reactFlowInstance = usePipelineStore((state) => state.reactFlowInstance);
  const setReactFlowInstance = usePipelineStore((state) => state.setReactFlowInstance);
  const currentPipelineId = usePipelineManagerStore((state) => state.currentPipelineId);
  const currentPipeline = usePipelineManagerStore((state) => state.currentPipeline);

  useEffect(() => {
    console.log(reactFlowInstance, currentPipeline);
    if (reactFlowInstance) {
      resetFlow({
        nodes: currentPipeline?.data?.nodes ?? [],
        edges: currentPipeline?.data?.edges ?? [],
        viewport: { zoom: 1, x: 0, y: 0 },
      });
    }
  }, [currentPipelineId, reactFlowInstance]);

  return (
    <div style={{height: '80vh'}}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        fitView
        fitViewOptions={fitViewOptions}
        nodeTypes={nodeTypes}
        onInit={setReactFlowInstance}
      >
        <Controls showZoom showFitView showInteractive position="bottom-left"/>
        {/*<MiniMap position="bottom-right"/>*/}
        <Background variant={BackgroundVariant.Dots} gap={12} size={1}/>
      </ReactFlow>
    </div>
  );
}
