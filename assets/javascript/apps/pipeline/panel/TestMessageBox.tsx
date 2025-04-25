import React, { useState } from "react";
import OverlayPanel from "../components/OverlayPanel";
import { apiClient } from "../api/api";
import usePipelineStore from "../stores/pipelineStore";

type TestMessageBoxParams = {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
};

export default function TestMessageBox({
  isOpen,
  setIsOpen,
}: TestMessageBoxParams) {
  const currentPipelineId = usePipelineStore(
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
    const message = newMessage.trim() || userMessage.trim();
    if (!message) {
      return;
    }
    setUserMessage(message);
    setNewMessage("");
    setErrorMessage("");
    clearEdgeLabels();
    setLoading(true);
    if (currentPipelineId) {
      apiClient.sendTestMessage(currentPipelineId, message).then((res) => {
        getMessageResponseUntilSuccess(currentPipelineId, res.task_id);
      });
    }
  }

  async function getMessageResponseUntilSuccess(
    pipelineId: number,
    taskId: string,
  ) {
    setLoading(true);
    let polling = true;

    while (polling) {
      try {
        const response = await apiClient.getTestMessageResponse(
          pipelineId,
          taskId,
        );
        if (
          response.complete &&
          response.success &&
          response.result &&
          typeof response.result !== "string"
        ) {
          // The task finished successfully and we receive the response
          const result = response.result;
          if (result.error) {
            setErrorMessage(result.error);
          } else {
            setResponseMessage(result.messages[result.messages.length - 1]);
            for (const [nodeId, nodeOutput] of Object.entries(result.outputs)) {
              setEdgeLabel(nodeId, nodeOutput.output_handle, nodeOutput.message);
            }
          }
          setLoading(false);
          polling = false;
        } else if (response.complete && !response.success) {
          // The task failed
          if (response.result) {
            const errorMessage =
              typeof response.result === "string"
                ? response.result
                : response.result.messages[0];
            setErrorMessage(errorMessage);
          }

          setLoading(false);
          polling = false;
        } else if (!response.complete) {
          // The task has not finishe dyet, wait for 1 second before fetching the response again
          await new Promise((resolve) => setTimeout(resolve, 1000));
        } else {
          polling = false;
        }
      } catch (error: unknown) {
        setLoading(false);
        if (error instanceof Error) {
          console.error(error.message);
          setErrorMessage(error.message);
          throw error;
        } else {
          console.error("Unexpected error", error);
          throw new Error("Unexpected error occurred");
        }
      }
    }
  }

  function togglePanel() {
    if (isOpen) {
      clear();
    }
    setIsOpen(!isOpen);
  }

  function onClear(e: React.MouseEvent<HTMLElement>) {
    e.preventDefault();
    clear();
  }

  function clear() {
    setNewMessage("");
    setUserMessage("");
    setResponseMessage("");
    setErrorMessage("");
    clearEdgeLabels();
  }

  return (
    <div className="relative">
      <button
        className="btn btn-circle btn-ghost absolute top-4 left-16 z-10 text-primary"
        onClick={togglePanel}
        title="Test Pipeline"
      >
        <i
          className={`fas ${
            isOpen ? "fa-circle-stop" : "fa-circle-play"
          } text-4xl shadow-md rounded-full`}
        />
      </button>

      <OverlayPanel
        classes="p-4 top-16 left-4 w-72 max-h-[70vh] overflow-y-auto"
        isOpen={isOpen}
      >
        {isOpen && (
          <>
            <h2 className="text-xl text-center font-bold">Send test message</h2>
            <div className="p-4">
              {userMessage && (
                <div className="mb-4">
                  <div className="p-2 border rounded-sm">
                    <strong>Input:</strong> {userMessage}
                  </div>
                  {loading ? (
                    <div className="mt-2 p-2 border rounded-sm">
                      <span className="loading loading-dots loading-sm"></span>
                    </div>
                  ) : errorMessage ? (
                    <div className="mt-2 p-2 border rounded-sm text-red-500">
                      <strong>Error:</strong> {errorMessage}
                    </div>
                  ) : (
                    responseMessage && (
                      <div className="mt-2 p-2 border rounded-sm">
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
                  className="input w-full p-2 border rounded-sm mb-2"
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Type your message..."
                />
                <div className="grid grid-cols-2">
                  <button className="btn btn-primary" type="submit">
                    Send
                  </button>
                  <button className="btn" onClick={onClear}>Clear</button>
                </div>
              </form>
            </div>
          </>
        )}
      </OverlayPanel>
    </div>
  );
}
