import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

let dialogSettings;
const dialogRoot = document.createElement("div");
vi.mock("alertifyjs", () => ({
    confirm: () => ({
        setting: (settings) => {
            dialogSettings = settings;
            return {show: () => {}, elements: {root: dialogRoot}};
        },
    }),
}));

await import("./alertify.js");

describe("confirmDialog", () => {
    it("resolves true when the user confirms", async () => {
        const result = window.confirmDialog("Delete?");
        dialogSettings.onok();
        expect(await result).toBe(true);
    });

    it("resolves false when the user cancels", async () => {
        const result = window.confirmDialog("Delete?");
        dialogSettings.oncancel();
        expect(await result).toBe(false);
    });

    it("resolves false when the dialog is closed without choosing", async () => {
        const result = window.confirmDialog("Delete?");
        dialogSettings.onclose();
        expect(await result).toBe(false);
    });

    describe("with a modal dialog open", () => {
        let modal;

        beforeEach(() => {
            document.body.innerHTML = `<dialog open></dialog>`;
            modal = document.querySelector("dialog");
            // jsdom has no showModal(), so stand in for the :modal match.
            const querySelector = document.querySelector.bind(document);
            vi.spyOn(document, "querySelector").mockImplementation((selector) =>
                selector === "dialog:modal" ? modal : querySelector(selector),
            );
        });

        afterEach(() => vi.restoreAllMocks());

        it("shows the confirm inside the modal so it can be clicked", () => {
            window.confirmDialog("Delete?");
            expect(dialogRoot.parentElement).toBe(modal);
        });

        it("keeps the modal open when Esc is pressed while confirming", () => {
            window.confirmDialog("Delete?");
            const cancel = new Event("cancel", {cancelable: true});
            modal.dispatchEvent(cancel);
            expect(cancel.defaultPrevented).toBe(true);
        });

        it("lets Esc close the modal again once the confirm is answered", () => {
            window.confirmDialog("Delete?");
            dialogSettings.oncancel();
            const cancel = new Event("cancel", {cancelable: true});
            modal.dispatchEvent(cancel);
            expect(cancel.defaultPrevented).toBe(false);
        });
    });

    it("shows the confirm on the page when no modal dialog is open", () => {
        document.body.innerHTML = "";
        window.confirmDialog("Delete?");
        expect(dialogRoot.parentElement).toBe(document.body);
    });
});

describe("confirmThenSubmit", () => {
    let form;
    let submissions;

    beforeEach(() => {
        document.body.innerHTML = `
            <form>
                <button type="submit" name="action_keep">Keep</button>
                <button type="submit" name="action_remove">Remove</button>
            </form>`;
        form = document.querySelector("form");
        form.addEventListener("submit", (event) => window.confirmThenSubmit(event, "Remove?"));
        submissions = [];
        form.addEventListener("submit", (event) => {
            submissions.push({prevented: event.defaultPrevented, submitter: event.submitter?.name});
            event.preventDefault();
        });
    });

    it("holds the submission until the user confirms", () => {
        form.querySelector("[name=action_remove]").click();

        expect(submissions).toEqual([{prevented: true, submitter: "action_remove"}]);
    });

    it("submits with the original submit button once confirmed", async () => {
        form.querySelector("[name=action_remove]").click();
        dialogSettings.onok();
        await Promise.resolve();

        expect(submissions.at(-1)).toEqual({prevented: false, submitter: "action_remove"});
    });

    it("does not submit when the user cancels", async () => {
        form.querySelector("[name=action_remove]").click();
        dialogSettings.oncancel();
        await Promise.resolve();

        expect(submissions).toHaveLength(1);
    });
});

describe("confirmThenTrigger", () => {
    beforeEach(() => {
        document.body.innerHTML = `<button id="archive-button">Archive</button>`;
        window.htmx = {trigger: vi.fn()};
    });

    it("triggers the confirmed event once the user confirms", async () => {
        window.confirmThenTrigger("archive-button", "Archive?");
        dialogSettings.onok();
        await Promise.resolve();

        expect(window.htmx.trigger).toHaveBeenCalledWith(document.getElementById("archive-button"), "confirmed");
    });

    it("does nothing when the user cancels", async () => {
        window.confirmThenTrigger("archive-button", "Archive?");
        dialogSettings.oncancel();
        await Promise.resolve();

        expect(window.htmx.trigger).not.toHaveBeenCalled();
    });
});
