import {describe, expect, it} from "vitest";
import {CompletionContext} from "@codemirror/autocomplete";
import {EditorState} from "@codemirror/state";
import {textEditorVarCompletions, toolNameCompletions} from "./codemirror-extensions.js";

const tools = [
  {name: "one-off-reminder", label: "One-off Reminder"},
  {name: "get_weather", label: "Weather: Today", note: "Name may get a suffix at runtime"},
];

function complete(source, doc, explicit = false) {
  return source(new CompletionContext(EditorState.create({doc}), doc.length, explicit));
}

function labels(doc, explicit = false) {
  return complete(toolNameCompletions(tools), doc, explicit).options.map((option) => option.label);
}

describe("toolNameCompletions", () => {
  it("matches a hyphenated prefix from the start of the word", () => {
    const result = complete(toolNameCompletions(tools), "Use the one-off-r");

    expect(result.from).toBe("Use the ".length);
    expect(labels("Use the one-off-r")).toEqual(["one-off-reminder"]);
  });

  it.each([
    ["the start of the name", "Use One", ["one-off-reminder"]],
    ["a later part of the name", "Use remind", ["one-off-reminder"]],
    ["a part after an underscore", "Use weath", ["get_weather"]],
  ])("offers a tool when the word matches %s", (_name, doc, expected) => {
    expect(labels(doc)).toEqual(expected);
  });

  it("offers every tool on request with nothing typed", () => {
    expect(labels("Use ", true)).toEqual(["one-off-reminder", "get_weather"]);
  });

  it("filters the options itself and inserts the plain name", () => {
    const result = complete(toolNameCompletions(tools), "one");
    const [option] = result.options;

    expect(result.filter).toBe(false);
    expect(option.apply).toBeUndefined();
    expect(option).toMatchObject({type: "function", detail: "One-off Reminder"});
    expect(option.section).toBeUndefined();
  });

  it.each([
    ["the note when there is one", 1, "Name may get a suffix at runtime"],
    ["that it is a tool otherwise", 0, "Tool enabled on this node. Inserted as plain text."],
  ])("describes %s", (_name, index, info) => {
    expect(complete(toolNameCompletions(tools), "Use ", true).options[index].info).toBe(info);
  });

  it.each([
    ["a word that only fuzzily matches a name", "Use the", false],
    ["a word in the middle of a part", "Use eather", false],
    ["a single character", "Use o", false],
    ["no word at the cursor", "Use ", false],
    ["a word inside braces", "Use {one", false],
    ["a word inside braces on request", "Use {one", true],
  ])("offers nothing for %s", (_name, doc, explicit) => {
    expect(complete(toolNameCompletions(tools), doc, explicit)).toBeNull();
  });
});

describe("textEditorVarCompletions", () => {
  it("still offers variables for a typed word", () => {
    const result = complete(textEditorVarCompletions(["participant_data"]), "Hello par");

    expect(result.from).toBe("Hello ".length);
    expect(result.options.map((option) => option.label)).toEqual(["participant_data"]);
  });
});
