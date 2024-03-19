import React, {useCallback} from 'react';
import ReactFlow, {
    addEdge,
    Background,
    BackgroundVariant,
    Connection,
    Controls,
    DefaultEdgeOptions,
    Edge,
    FitViewOptions,
    Node,
    NodeTypes,
    useEdgesState,
    useNodesState
} from 'reactflow';

import {MyCustomNode} from './CustomNode';

import 'reactflow/dist/style.css';

const initialNodes: Node[] = [
    {id: '1', position: {x: 0, y: 0}, data: {label: '1', value: 123}, type: 'custom'},
    {id: '2', position: {x: 0, y: 100}, data: {label: '2'}},
];
const initialEdges: Edge[] = [{id: 'e1-2', source: '1', target: '2'}];

const fitViewOptions: FitViewOptions = {
    padding: 0.2,
};

const defaultEdgeOptions: DefaultEdgeOptions = {
    animated: false,
};

const nodeTypes: NodeTypes = {
    custom: MyCustomNode,
};

export default function Flow() {
    const [nodes, , onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

    const onConnect = useCallback(
        (connection: Edge | Connection) => setEdges((eds) => addEdge(connection, eds)),
        [setEdges],
    );

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
                defaultEdgeOptions={defaultEdgeOptions}
                nodeTypes={nodeTypes}
            >
                <Controls showZoom showFitView showInteractive position="bottom-left"/>
                {/*<MiniMap position="bottom-right"/>*/}
                <Background variant={BackgroundVariant.Dots} gap={12} size={1}/>
            </ReactFlow>
        </div>
    );
}
