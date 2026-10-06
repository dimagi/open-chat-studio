import {beforeEach, describe, expect, it, vi} from "vitest";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import ImprovePromptSection from "./ImprovePromptSection";
import {apiClient} from "../api/api";

// CodeDiffEditor mounts a real CodeMirror view, which is not reliable in jsdom. The stub exposes
// its props and lets a test edit the proposal.
vi.mock("../components/CodeDiffEditor", () => ({
  CodeDiffEditor: ({original, value, onChange}: {original: string; value: string; onChange: (value: string) => void}) => (
    <textarea data-testid="prompt-diff" data-original={original} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const CURRENT = "Help people. {participant_data}";
const REWRITE = "You are a support assistant. {participant_data}";

function renderSection(onAccept = vi.fn()) {
  render(
    <ImprovePromptSection
      show={true}
      currentPrompt={CURRENT}
      nodeType="llm"
      toolNames={["one-off-reminder"]}
      autocompleteVars={["participant_data"]}
      onAccept={onAccept}
    />,
  );
  return onAccept;
}

async function improve() {
  fireEvent.click(screen.getByText("Improve"));
  await waitFor(() => expect(screen.queryByTestId("prompt-diff")).toBeInTheDocument());
}

describe("ImprovePromptSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing while hidden", () => {
    render(
      <ImprovePromptSection show={false} currentPrompt={CURRENT} nodeType="llm" toolNames={[]} autocompleteVars={[]} onAccept={vi.fn()} />,
    );

    expect(screen.queryByText("Improve")).toBeNull();
  });

  it("sends the prompt with the node's context and the instruction", async () => {
    const improvePrompt = vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: REWRITE, notes: []}});
    renderSection();

    fireEvent.change(screen.getByPlaceholderText(/What should change/), {target: {value: "Make it formal"}});
    await improve();

    expect(improvePrompt).toHaveBeenCalledWith({
      prompt: CURRENT,
      node_type: "llm",
      tool_names: ["one-off-reminder"],
      instruction: "Make it formal",
    });
  });

  it("shows the notes and a diff against the current prompt", async () => {
    vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: REWRITE, notes: ["Stated the role."]}});
    renderSection();

    await improve();

    expect(screen.getByText("Stated the role.")).toBeInTheDocument();
    expect(screen.getByTestId("prompt-diff")).toHaveAttribute("data-original", CURRENT);
    expect(screen.getByTestId("prompt-diff")).toHaveValue(REWRITE);
  });

  it("Accept passes on the proposal as the user edited it", async () => {
    vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: REWRITE, notes: []}});
    const onAccept = renderSection();
    await improve();

    fireEvent.change(screen.getByTestId("prompt-diff"), {target: {value: REWRITE + " Be brief."}});
    fireEvent.click(screen.getByText("Accept"));

    expect(onAccept).toHaveBeenCalledWith(REWRITE + " Be brief.");
    expect(screen.queryByTestId("prompt-diff")).toBeNull();
  });

  it("Reject discards the proposal without changing the prompt", async () => {
    vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: REWRITE, notes: ["Stated the role."]}});
    const onAccept = renderSection();
    await improve();

    fireEvent.click(screen.getByText("Reject"));

    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.queryByTestId("prompt-diff")).toBeNull();
    expect(screen.queryByText("Stated the role.")).toBeNull();
  });

  it("shows only the notes when the prompt comes back unchanged", async () => {
    vi.spyOn(apiClient, "improvePrompt").mockResolvedValue({response: {prompt: CURRENT, notes: ["Nothing to change."]}});
    renderSection();

    fireEvent.click(screen.getByText("Improve"));

    await waitFor(() => expect(screen.getByText("Nothing to change.")).toBeInTheDocument());
    expect(screen.queryByTestId("prompt-diff")).toBeNull();
    expect(screen.queryByText("Accept")).toBeNull();
  });

  it("ignores a response that arrives after Clear", async () => {
    let resolve: (value: {response: {prompt: string; notes: string[]}}) => void = () => {};
    vi.spyOn(apiClient, "improvePrompt").mockReturnValue(new Promise((r) => { resolve = r; }));
    renderSection();

    fireEvent.click(screen.getByText("Improve"));
    fireEvent.click(screen.getByText("Clear"));
    resolve({response: {prompt: REWRITE, notes: []}});

    await waitFor(() => expect(screen.getByText("Improve")).not.toBeDisabled());
    expect(screen.queryByTestId("prompt-diff")).toBeNull();
  });

  it("does not send a second request while one is running", () => {
    const improvePrompt = vi.spyOn(apiClient, "improvePrompt").mockReturnValue(new Promise(() => {}));
    renderSection();

    fireEvent.click(screen.getByText("Improve"));
    fireEvent.click(screen.getByText("Improve"));

    expect(improvePrompt).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["the server's message", () => Promise.reject({error: "An error occurred."}), "An error occurred."],
    ["a fallback for an empty response", () => Promise.resolve({}), "No suggestion was returned. Please try again."],
  ])("shows %s when the request fails", async (_name, respond, message) => {
    vi.spyOn(apiClient, "improvePrompt").mockImplementation(respond);
    renderSection();

    fireEvent.click(screen.getByText("Improve"));

    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument());
  });
});
