import React from "react";
import Component from "./Component";
import { NodeParams } from "../types/nodeParams";

export default function SidePanel(props: { inputTypes }) {
    function onDragStart(
        event: React.DragEvent<any>,
        data: { type: string, label: string, inputParams: NodeParams[] }
    ): void {
        event.dataTransfer.setData("nodedata", JSON.stringify(data));
    }

    return (
        <div className="join join-vertical w-full">
            {props.inputTypes.map((inputType) => {
                return <Component
                    key={inputType.name}
                    label={inputType.human_name}
                    onDragStart={(event) =>
                        onDragStart(event, {
                            label: inputType.human_name,
                            inputParams: inputType.input_params,
                            type: inputType.name,
                        })
                    } />
            })}
        </div>
    )
}
