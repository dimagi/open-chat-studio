/** @vitest-environment jsdom */

import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {sessionExport} from "./session-export.js";

describe("sessionExport", () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("shows success only after the export response sets its token cookie", () => {
        const component = sessionExport();
        const link = document.createElement("a");
        link.href = "/export";
        const event = {currentTarget: link, preventDefault: vi.fn()};

        component.start(event);

        expect(component.exportState).toBe("loading");
        expect(link.href).toContain("download_token=");

        document.cookie = `session_export_download_${component.downloadToken}=1; path=/`;
        vi.advanceTimersByTime(100);

        expect(component.exportState).toBe("success");
        expect(event.preventDefault).not.toHaveBeenCalled();
    });

    it("reports an error when the export response never signals a download", () => {
        const component = sessionExport();
        const link = document.createElement("a");

        component.start({currentTarget: link, preventDefault: vi.fn()});
        vi.advanceTimersByTime(15000);

        expect(component.exportState).toBe("error");
    });
});
