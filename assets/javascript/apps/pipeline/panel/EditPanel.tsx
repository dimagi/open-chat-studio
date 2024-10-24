import React from "react";
import useEditorStore from "../stores/editorStore";
import OverlayPanel from "../components/OverlayPanel";


export default function EditPanel() {
  const isOpen = useEditorStore((state) => state.isOpen);
  const closeEditor = useEditorStore((state) => state.closeEditor);
  return (
    <div className="relative">
      <OverlayPanel classes="top-0 right-0 w-2/5 h-[80vh] overflow-hidden" isOpen={isOpen}>
        {isOpen && (
          <>
            <div className="absolute top-0 right-0">
              <button
                className="btn btn-xs btn-ghost"
                onClick={closeEditor}
              >
                <i className="fa fa-times"></i>
              </button>
            </div>
            <h2 className="text-lg text-center font-bold">Editor</h2>
          </>
        )}
      </OverlayPanel>
    </div>
  );
}
