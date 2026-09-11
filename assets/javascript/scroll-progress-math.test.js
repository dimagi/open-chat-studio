import {describe, expect, it} from "vitest";

import {computeMessageProgress} from "./scroll-progress-math.js";

describe("computeMessageProgress", () => {
    it("is near 0 at the first message", () => {
        // indices are 1-based, so message 1 of 50 isn't exactly 0
        expect(computeMessageProgress({currentIndex: 1, totalMessages: 50})).toBe(0.02);
    });

    it("is 0.5 halfway through the conversation", () => {
        expect(computeMessageProgress({currentIndex: 25, totalMessages: 50})).toBe(0.5);
    });

    it("is 1 at the last message", () => {
        expect(computeMessageProgress({currentIndex: 50, totalMessages: 50})).toBe(1);
    });

    it("clamps to 1 if currentIndex somehow exceeds the total", () => {
        expect(computeMessageProgress({currentIndex: 999, totalMessages: 50})).toBe(1);
    });

    it("is 0 when there are no messages", () => {
        expect(computeMessageProgress({currentIndex: 0, totalMessages: 0})).toBe(0);
    });

    it("only advances when the reader's position advances, not as more pages load", () => {
        const total = 100;
        const before = computeMessageProgress({currentIndex: 10, totalMessages: total});
        const same = computeMessageProgress({currentIndex: 10, totalMessages: total});
        expect(same).toBe(before);
        const furtherIn = computeMessageProgress({currentIndex: 50, totalMessages: total});
        expect(furtherIn).toBeGreaterThan(before);
    });
});
