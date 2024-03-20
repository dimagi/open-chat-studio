import React from "react";
import PanelGroup from "./PanelGroup";
import Component from "./Component";


export default function SidePanel() {
  function onDragStart(
    event: React.DragEvent<any>,
    data: { type: string }
  ): void {
    //start drag event
    const crt = event.currentTarget.cloneNode(true);
    crt.style.position = "absolute";
    crt.style.top = "-500px";
    crt.style.right = "-500px";
    crt.classList.add("cursor-grabbing");
    document.body.appendChild(crt);
    event.dataTransfer.setDragImage(crt, 0, 0);
    event.dataTransfer.setData("nodedata", JSON.stringify(data));
  }

  return (
    <div className="join join-vertical w-full">
      <PanelGroup name="Inputs">
        <Component
          label="File"
          onDragStart={(event) =>
            onDragStart(event, {
              type: "file",
            })
          }/>
      </PanelGroup>
      <PanelGroup name="Steps">
        <p>hello</p>
      </PanelGroup>
    </div>
  )
}
