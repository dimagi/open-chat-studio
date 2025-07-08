import "reactflow/dist/style.css";
import "./styles.css"
import React, {useCallback, useState, useEffect} from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  EdgeTypes,
  FitViewOptions,
  MarkerType,
  NodeDragHandler,
  NodeTypes,
  OnSelectionChangeParams,
  PanOnScrollMode,
} from "reactflow";

import {PipelineNode} from "./PipelineNode";
import ComponentList from "./panel/ComponentList";
import usePipelineStore from "./stores/pipelineStore";
import {getCachedData, getNodeId} from "./utils";
import {useHotkeys} from "react-hotkeys-hook";
import EditPanel from "./panel/EditPanel";
import useEditorStore from "./stores/editorStore";
import TestMessageBox from "./panel/TestMessageBox";
import AnnotatedEdge from "./AnnotatedEdge";
import { EndNode, StartNode } from "./BoundaryNode";
import { NodeData } from "./types/nodeParams";

const fitViewOptions: FitViewOptions = {
  padding: 0.2,
};

const nodeTypes: NodeTypes = {
  pipelineNode: PipelineNode,
  startNode: StartNode,
  endNode: EndNode,
};

const edgeTypes: EdgeTypes = {
  annotatedEdge: AnnotatedEdge,
};

export default function Pipeline() {
  const nodes = usePipelineStore((state) => state.nodes);
  const edges = usePipelineStore((state) => state.edges);
  const readOnly = usePipelineStore((state) => state.readOnly);
  const setReadOnly = usePipelineStore((state) => state.setReadOnly);
  const onNodesChange = usePipelineStore((state) => state.onNodesChange);
  const onEdgesChange = usePipelineStore((state) => state.onEdgesChange);
  const onConnect = usePipelineStore((state) => state.onConnect);
  const setNodes = usePipelineStore((state) => state.setNodes);
  const addNode = usePipelineStore((state) => state.addNode);
  const deleteEdge = usePipelineStore((state) => state.deleteEdge);
  const deleteNode = usePipelineStore((state) => state.deleteNode);
  const reactFlowInstance = usePipelineStore((state) => state.reactFlowInstance);
  const setReactFlowInstance = usePipelineStore((state) => state.setReactFlowInstance);
  const currentPipeline = usePipelineStore((state) => state.currentPipeline);
  const autoSaveCurrentPipline = usePipelineStore((state) => state.autoSaveCurrentPipline);
  const savePipeline = usePipelineStore((state) => state.savePipeline);
  const { nodeSchemas } = getCachedData();

  const editingNode = useEditorStore((state) => state.currentNode);

  const [lastSelection, setLastSelection] = useState<OnSelectionChangeParams | null>(null);
  const [selectedOverlay, setSelectedOverlay] = useState<string | null>(null);

  useEffect(() => {
    const readOnlyValue = JSON.parse(document.getElementById("read-only")?.textContent || "false");
    setReadOnly(readOnlyValue);
  }, [setReadOnly]);

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
        const data: NodeData = JSON.parse(
          event.dataTransfer.getData("nodedata")
        );
        const newId = getNodeId(data.type);
        data.params["name"] = newId;
        const flowType = data.flowType;
        delete data.flowType;

        const newNode = {
          id: newId,
          type: flowType,
          position: {x: 0, y: 0},
          data: {
            ...data,
            id: newId,
          },
        };
        addNode(newNode, {x: event.clientX, y: event.clientY});

        // Close the panel after adding the node
        setSelectedOverlay(null);
      }
    },
    [getNodeId, setNodes, addNode]
  );

  const onNodeDragStop: NodeDragHandler = useCallback(() => {
    autoSaveCurrentPipline(nodes, edges);
  }, [autoSaveCurrentPipline, nodes, edges, reactFlowInstance]);

  function handleDelete(e: KeyboardEvent) {
    if (lastSelection) {
      e.preventDefault();
      (e as unknown as Event).stopImmediatePropagation();
      deleteNode(
          lastSelection.nodes.filter((node) => nodeSchemas.get(node.data.type)!["ui:can_delete"]).map((node) => node.id)
      );
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
    setSelectedOverlay(null);
  }, [selectedOverlay]);

  return (
    <div className="h-[80vh]">
      <ReactFlow
        edgesUpdatable={!readOnly}
        edgesFocusable={!readOnly}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        fitViewOptions={fitViewOptions}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
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
        {!readOnly && (
          <>
            <ComponentList
              isOpen={selectedOverlay == "componentList"}
              setIsOpen={(open) => setSelectedOverlay(open ? "componentList" : null)}
            />
            <TestMessageBox
              isOpen={selectedOverlay == "textBox"}
              setIsOpen={(open) => setSelectedOverlay(open ? "textBox" : null)}
            />
          </>
        )}
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
