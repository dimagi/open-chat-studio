import React, { useCallback, useEffect, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  FitViewOptions,
  MarkerType,
  NodeDragHandler,
  NodeTypes,
  OnMove,
  OnSelectionChangeParams,
} from "reactflow";

import { PipelineNode } from "./PipelineNode";
import ComponentList from "./panel/ComponentList";
import { NodeInputTypes } from "./types/nodeInputTypes";
import "reactflow/dist/style.css";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import usePipelineStore from "./stores/pipelineStore";
import { getNodeId } from "./utils";
import { useHotkeys } from "react-hotkeys-hook";

const fitViewOptions: FitViewOptions = {
  padding: 0.2,
};

const nodeTypes: NodeTypes = {
  pipelineNode: PipelineNode,
};

export default function Pipeline(props: { inputTypes: NodeInputTypes[] }) {
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);
  const onNodesChange = usePipelineStore((state) => state.onNodesChange);
  const onEdgesChange = usePipelineStore((state) => state.onEdgesChange);
  const onConnect = usePipelineStore((state) => state.onConnect);
  const resetFlow = usePipelineStore((state) => state.resetFlow);
  const setNodes = usePipelineStore((state) => state.setNodes);
  const addNode = usePipelineStore((state) => state.addNode);
  const deleteEdge = usePipelineStore((state) => state.deleteEdge);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const reactFlowInstance = usePipelineStore((state) => state.reactFlowInstance);
  const setReactFlowInstance = usePipelineStore((state) => state.setReactFlowInstance);
  const currentPipelineId = usePipelineManagerStore((state) => state.currentPipelineId);
  const currentPipeline = usePipelineManagerStore((state) => state.currentPipeline);
  const autoSaveCurrentPipline = usePipelineManagerStore((state) => state.autoSaveCurrentPipline);
  const [lastSelection, setLastSelection] = useState<OnSelectionChangeParams | null>(null);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (reactFlowInstance) {
      resetFlow({
        nodes: currentPipeline?.data?.nodes ?? [],
        edges: currentPipeline?.data?.edges ?? [],
        viewport: currentPipeline?.data?.viewport ?? {zoom: 1, x: 0, y: 0},
      });
    }
  }, [currentPipelineId, reactFlowInstance]);

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer.types.some((types) => types === "nodedata")) {
      event.dataTransfer.dropEffect = "move";
    } else {
      event.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      if (event.dataTransfer.types.some((types) => types === "nodedata")) {
        const data: { type: string } = JSON.parse(
          event.dataTransfer.getData("nodedata")
        );
        const newId = getNodeId(data.type);

        const newNode = {
          id: newId,
          type: "pipelineNode",
          position: {x: 0, y: 0},
          data: {
            ...data,
            id: newId,
          },
        };
        addNode(newNode, {x: event.clientX, y: event.clientY});

        // Close the panel after adding the node
        setIsOpen(false);
      }
    },
    [getNodeId, setNodes, addNode]
  );

  const onNodeDragStop: NodeDragHandler = useCallback(() => {
    autoSaveCurrentPipline(nodes, edges, reactFlowInstance?.getViewport()!);
  }, [autoSaveCurrentPipline, nodes, edges, reactFlowInstance]);

  const onMoveEnd: OnMove = useCallback(() => {
    autoSaveCurrentPipline(nodes, edges, reactFlowInstance?.getViewport()!);
  }, [autoSaveCurrentPipline, nodes, edges, reactFlowInstance]);

  function handleDelete(e: KeyboardEvent) {
    if (lastSelection) {
      e.preventDefault();
      (e as unknown as Event).stopImmediatePropagation();
      deleteNode(lastSelection.nodes.map((node) => node.id));
      deleteEdge(lastSelection.edges.map((edge) => edge.id));
    }
  }

  useHotkeys("backspace", handleDelete);

  const onSelectionChange = useCallback(
    (flow: OnSelectionChangeParams): void => {
      setLastSelection(flow);
    },
    [],
  );

  const defaultEdgeOptions = {
    markerEnd: {
      type: MarkerType.ArrowClosed,
    },
  };

  return (
    <div style={{ height: "80vh" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        fitViewOptions={fitViewOptions}
        nodeTypes={nodeTypes}
        onInit={setReactFlowInstance}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onMoveEnd={onMoveEnd}
        onNodeDragStop={onNodeDragStop}
        minZoom={0.01}
        maxZoom={8}
        deleteKeyCode={[]}
        defaultEdgeOptions={defaultEdgeOptions}
        onSelectionChange={onSelectionChange}
        onPaneClick={() => setIsOpen(false)} // Close panel when clicking on the canvas
      >
        <ComponentList
          inputTypes={props.inputTypes}
          isOpen={isOpen}
          setIsOpen={setIsOpen}
        />
        <Controls showZoom showFitView showInteractive position="bottom-left" />
        <Background
          variant={BackgroundVariant.Dots}
          gap={12}
          size={1}
        />
      </ReactFlow>
    </div>
  );
}
