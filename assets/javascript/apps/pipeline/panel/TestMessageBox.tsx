import React, { useState } from "react";
import OverlayPanel from "../components/OverlayPanel";
import { apiClient } from "../api/api";
import usePipelineStore from "../stores/pipelineStore";

type TestMessageBoxParams = {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
};

type ResponseMessage = {
  message?: string;
  className?: string;
  prefix?: string;
}

export default function TestMessageBox({
  isOpen,
  setIsOpen,
}: TestMessageBoxParams) {
  const currentPipelineId = usePipelineStore(
    (state) => state.currentPipelineId,
  );
  const maxInputChars = usePipelineStore((state) => state.currentPipeline?.max_char_limit ?? null);
  const setEdgeLabel = usePipelineStore((state) => state.setEdgeLabel);
  const clearEdgeLabels = usePipelineStore((state) => state.clearEdgeLabels);
  const [newMessage, setNewMessage] = useState("");
  const [userMessage, setUserMessage] = useState("");
  const [responseMessage, setResponseMessage] = useState<ResponseMessage>({});
  const [loading, setLoading] = useState(false);
  const isOverLimit = maxInputChars !== null && newMessage.length > maxInputChars;
  const counterColor = maxInputChars
    ? isOverLimit
      ? "text-red-500"
      : newMessage.length > maxInputChars * 0.8
        ? "text-yellow-500"
        : "text-gray-500"
    : "text-gray-500";

  const setError = (message: string) => {
    setResponseMessage({ message, className: "text-red-500", prefix: "Error:" });
  }

  function sendMessage() {
    const message = newMessage.trim() || userMessage.trim();
    if (!message || isOverLimit) {
      return;
    }
    setUserMessage(message);
    setNewMessage("");
    clearEdgeLabels();
    setResponseMessage({});
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
          const result = response.result;
          if (result.error) {
            setError(result.error);
          } else if (result.interrupt) {
            setResponseMessage({message: result.interrupt.message, className: "text-yellow-500", prefix: "Interrupt:"});
            for (const nodeOutput of Object.values(result.outputs)) {
                setEdgeLabel(nodeOutput.node_id, nodeOutput.output_handle, nodeOutput.message);
            }
          } else {
            setResponseMessage({message: result.messages[result.messages.length - 1], prefix: "Output:"});
            for (const nodeOutput of Object.values(result.outputs)) {
                setEdgeLabel(nodeOutput.node_id, nodeOutput.output_handle, nodeOutput.message);
            }
          }
          setLoading(false);
          polling = false;
        } else if (response.complete && !response.success) {
          if (response.result) {
            const errorMessage =
              typeof response.result === "string"
                ? response.result
                : response.result.messages[0];
            setError(errorMessage);
          }
          setLoading(false);
          polling = false;
        } else if (!response.complete) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
        } else {
          polling = false;
        }
      } catch (error: unknown) {
        setLoading(false);
        if (error instanceof Error) {
          console.error(error.message);
          setError(error.message);
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
    setResponseMessage({});
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
                  ) : (
                    responseMessage?.message && (
                      <div className={`mt-2 p-2 border rounded-sm ${responseMessage.className || ""}`}>
                        <strong>{responseMessage.prefix}</strong> {responseMessage.message}
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
                  className="input w-full p-2 border rounded-sm mb-1"
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Type your message..."
                />
                {maxInputChars && (
                  <div className={`text-xs text-right mb-2 ${counterColor}`}>
                    {newMessage.length} / {maxInputChars} chars
                    {isOverLimit && <span className="ml-1">(too long)</span>}
                  </div>
                )}
                <div className="grid grid-cols-2">
                  <button className="btn btn-primary" type="submit" disabled={isOverLimit}>
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
