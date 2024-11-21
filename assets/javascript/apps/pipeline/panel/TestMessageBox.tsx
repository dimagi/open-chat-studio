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
  const [errorMessage, setErrorMessage] = useState("");
  const [loading, setLoading] = useState(false);

  function sendMessage() {
    if (!newMessage.trim()) {
      return;
    }
    setUserMessage(newMessage);
    setNewMessage("");
    setErrorMessage("");
    clearEdgeLabels();
    setLoading(true);

    apiClient.sendTestMessage(currentPipelineId, newMessage).then((res) => {
      getMessageResponseUntilSuccess(currentPipelineId, res.task_id);
    });
  }

  async function getMessageResponseUntilSuccess(pipelineId, taskId) {
    setLoading(true);
    let polling = true;

    while (polling) {
      try {
        const response = await apiClient.getTestMessageResponse(
          pipelineId,
          taskId,
        );
        if (response.complete && response.success) {
          // The task finished succesfully and we receive the response
          const result = response.result;
          setResponseMessage(result.messages[result.messages.length - 1]);
          for (const [nodeId, message] of Object.entries(result.outputs)) {
            setEdgeLabel(nodeId, message);
          }
          setLoading(false);
          polling = false;
        } else if (response.complete && !response.success) {
          // The task failed
          setErrorMessage(response.result);
          setLoading(false);
          polling = false;
        } else if (!response.complete) {
          // The task has not finishe dyet, wait for 1 second before fetching the response again
          await new Promise((resolve) => setTimeout(resolve, 1000));
        } else {
          polling = false;
        }
      } catch (error) {
        console.error("Error fetching message response:", error);
        setErrorMessage(error.toString());
        setLoading(false);
        break;
      }
    }
  }

  function togglePanel() {
    if (isOpen) {
      // When closing the panel, clear the chat box and the annotations
      clearEdgeLabels();
      setUserMessage("");
      setResponseMessage("");
      setErrorMessage("");
    }
    setIsOpen(!isOpen);
  }

  return (
    <div className="relative">
      <button
        className="absolute top-4 left-16 z-10 text-4xl text-primary"
        onClick={togglePanel}
        title="Test Pipeline"
      >
        <i
          className={`fas ${
            isOpen ? "fa-circle-minus" : "fa-circle-play"
          } text-4xl shadow-md rounded-full`}
        />
      </button>

      <OverlayPanel
        classes="top-16 left-4 w-72 max-h-[70vh] overflow-y-auto"
        isOpen={isOpen}
      >
        {isOpen && (
          <>
            <h2 className="text-xl text-center font-bold">Send test message</h2>
            <div className="p-4">
              {userMessage && (
                <div className="mb-4">
                  <div className="p-2 border rounded">
                    <strong>Input:</strong> {userMessage}
                  </div>
                  {loading ? (
                    <div className="mt-2 p-2 border rounded">
                      <span className="loading loading-dots loading-sm"></span>
                    </div>
                  ) : errorMessage ? (
                    <div className="mt-2 p-2 border rounded text-red-500">
                      <strong>Error:</strong> {errorMessage}
                    </div>
                  ) : (
                    responseMessage && (
                      <div className="mt-2 p-2 border rounded">
                        <strong>Output:</strong> {responseMessage}
                      </div>
                    )
                  )}
                </div>
              )}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
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
