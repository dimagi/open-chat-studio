/** @vitest-environment jsdom */

import {beforeEach, describe, expect, it, vi} from "vitest";

import htmx from "htmx.org";

import {setupScrollProgress} from "./scroll-progress.js";

vi.mock("htmx.org", () => ({
    default: {
        off: vi.fn(),
        on: vi.fn(),
    },
}));

describe("setupScrollProgress", () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <div id="scroll-progress-bar" role="progressbar" aria-valuenow="0" aria-valuetext="0 percent">
                <div id="scroll-progress-fill"></div>
            </div>
            <div id="table-top" data-total-messages="10">
                <div data-message-index="5"></div>
            </div>
        `;
        Object.defineProperty(window, "scrollY", {configurable: true, value: 100});
        Object.defineProperty(window, "innerHeight", {configurable: true, value: 800});
        Object.defineProperty(document.documentElement, "scrollHeight", {configurable: true, value: 2000});
        document.querySelector("[data-message-index]").getBoundingClientRect = () => ({bottom: 300});
    });

    it("updates the bar and accessible progress value from the visible message", () => {
        setupScrollProgress("#table-top");

        expect(document.querySelector("#scroll-progress-fill").style.width).toBe("50%");
        expect(document.querySelector("#scroll-progress-bar")).toHaveAttribute("aria-valuenow", "50");
        expect(document.querySelector("#scroll-progress-bar")).toHaveAttribute("aria-valuetext", "50 percent");
    });

    it("cleans up listeners and observers when the page is hidden", () => {
        const disconnect = vi.fn();
        vi.stubGlobal(
            "ResizeObserver",
            class {
                constructor() {
                    this.disconnect = disconnect;
                }

                observe() {}
            },
        );

        setupScrollProgress("#table-top");
        window.dispatchEvent(new Event("pagehide"));

        expect(disconnect).toHaveBeenCalledOnce();
        expect(htmx.off).toHaveBeenCalledWith("htmx:afterSettle", expect.any(Function));
    });
});
