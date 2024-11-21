import React, {useCallback, useEffect, useState} from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  FitViewOptions,
  MarkerType,
  NodeDragHandler,
  NodeTypes,
  OnSelectionChangeParams,
  PanOnScrollMode,
} from "reactflow";

import {PipelineNode} from "./PipelineNode";
import ComponentList from "./panel/ComponentList";
import "reactflow/dist/style.css";
import usePipelineManagerStore from "./stores/pipelineManagerStore";
import usePipelineStore from "./stores/pipelineStore";
import {getNodeId} from "./utils";
import {useHotkeys} from "react-hotkeys-hook";
import EditPanel from "./panel/EditPanel";
import useEditorStore from "./stores/editorStore";

const fitViewOptions: FitViewOptions = {
  padding: 0.2,
};

const nodeTypes: NodeTypes = {
  pipelineNode: PipelineNode,
};

export default function Pipeline() {
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
  const savePipeline = usePipelineManagerStore((state) => state.savePipeline);

  const editingNode = useEditorStore((state) => state.currentNode);

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

  function handleDelete(e: KeyboardEvent) {
    if (lastSelection) {
      e.preventDefault();
      (e as unknown as Event).stopImmediatePropagation();
      deleteNode(lastSelection.nodes.map((node) => node.id));
      deleteEdge(lastSelection.edges.map((edge) => edge.id));
    }
  }

  function manualSaveCurrentPipeline() {
    if (currentPipeline) {
      const updatedPipeline = {...currentPipeline, data: {nodes, edges}}
      savePipeline(updatedPipeline);
    }
  }

  useHotkeys(["backspace", "delete"], handleDelete);
  useHotkeys("ctrl+s", () => manualSaveCurrentPipeline(), {preventDefault: true});

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

  const handlePaneClick = useCallback(() => {
    setIsOpen(false);
  }, [setIsOpen]);

  return (
    <div className="h-[80vh]">
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
        onNodeDragStop={onNodeDragStop}
        minZoom={0.01}
        maxZoom={8}
        deleteKeyCode={[]}
        defaultEdgeOptions={defaultEdgeOptions}
        onSelectionChange={onSelectionChange}
        onPaneClick={handlePaneClick} // Close panel when clicking on the canvas
        panOnScroll={true}
        panOnScrollMode={PanOnScrollMode.Free}
        fitView={true}
      >
        <ComponentList
          isOpen={isOpen}
          setIsOpen={setIsOpen}
        />
        {editingNode && <EditPanel key={editingNode.id} nodeId={editingNode.id} />}
        <Controls showZoom showFitView showInteractive position="bottom-left"/>
        <Background
          variant={BackgroundVariant.Dots}
          gap={12}
          size={1}
        />
      </ReactFlow>
    </div>
  );
}
