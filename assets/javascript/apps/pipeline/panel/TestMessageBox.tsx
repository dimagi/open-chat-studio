import React, { useState } from "react";
import OverlayPanel from "../components/OverlayPanel";
import { apiClient } from "../api/api";
import usePipelineManagerStore from "../stores/pipelineManagerStore";
import usePipelineStore from "../stores/pipelineStore";

export default function TestMessageBox({ isOpen, setIsOpen }) {
  const currentPipelineId = usePipelineManagerStore(
    (state) => state.currentPipelineId,
  );
  const setEdgeLabel = usePipelineStore((state) => state.setEdgeLabel);
  const clearEdgeLabels = usePipelineStore((state) => state.clearEdgeLabels);
  const [newMessage, setNewMessage] = useState("");
  const [userMessage, setUserMessage] = useState("");
  const [responseMessage, setResponseMessage] = useState("");
  const [loading, setLoading] = useState(false);

  function sendMessage() {
    if (!newMessage.trim()) {
      return;
    }
    setUserMessage(newMessage);
    setNewMessage("");
    clearEdgeLabels();
    setLoading(true);

    apiClient.sendTestMessage(currentPipelineId, newMessage).then((res) => {
      setResponseMessage(res.data.messages[res.data.messages.length - 1]);
      for (const [nodeId, message] of Object.entries(res.data.outputs)) {
        setEdgeLabel(nodeId, message);
      }
      setLoading(false);
    });
  }

  function togglePanel() {
    if (isOpen) {
      clearEdgeLabels();
      setUserMessage("");
      setResponseMessage("");
    }
    setIsOpen(!isOpen);
  }

  return (
    <div className="relative">
      <button
        className="absolute top-16 left-4 z-10 text-4xl text-primary"
        onClick={togglePanel}
        title="Chat"
      >
        <i
          className={`fas ${
            isOpen ? "fa-circle-minus" : "fa-circle-play"
          } text-4xl shadow-md rounded-full`}
        />
      </button>

      <OverlayPanel
        classes="top-16 left-16 w-72 max-h-[70vh] overflow-y-auto"
        isOpen={isOpen}
      >
        {isOpen && (
          <>
            <div className="p-4">
              {userMessage && (
                <div className="mb-4">
                  <div className="p-2 border rounded">
                    <strong>You:</strong> {userMessage}
                  </div>
                  {loading ? (
                    <div className="mt-2 p-2 border rounded">
                      <span className="loading loading-dots loading-sm"></span>
                    </div>
                  ) : (
                    responseMessage && (
                      <div className="mt-2 p-2 border rounded">
                        <strong>Response:</strong> {responseMessage}
                      </div>
                    )
                  )}
                </div>
              )}
              <form
                onSubmit={(e) => {
                  e.preventDefault(); // Prevent the form from refreshing the page
                  sendMessage();
                }}
                className="w-full"
              >
                <input
                  type="text"
                  className="w-full p-2 border rounded mb-2"
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Type your message..."
                />
                <button className="w-full btn btn-primary" type="submit">
                  Send Message
                </button>
              </form>
            </div>
          </>
        )}
      </OverlayPanel>
    </div>
  );
}
