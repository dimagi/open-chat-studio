import {describe, expect, it} from "vitest";

import {computeProgress} from "./scroll-progress-math.js";

describe("computeProgress", () => {
    it("is 0 before the container's top is reached", () => {
        expect(computeProgress({containerTop: 500, containerHeight: 3000, viewportHeight: 800, scrollY: 100})).toBe(0);
    });

    it("is 0.5 halfway through the scrollable range", () => {
        // scrollable = 3000 - 800 = 2200, halfway = 500 + 1100 = 1600
        expect(computeProgress({containerTop: 500, containerHeight: 3000, viewportHeight: 800, scrollY: 1600})).toBe(0.5);
    });

    it("clamps to 1 once scrolled past the end", () => {
        expect(computeProgress({containerTop: 500, containerHeight: 3000, viewportHeight: 800, scrollY: 999999})).toBe(1);
    });

    it("is 0 when the container fits entirely in the viewport", () => {
        expect(computeProgress({containerTop: 0, containerHeight: 400, viewportHeight: 800, scrollY: 0})).toBe(0);
    });

    it("recomputes correctly as containerHeight grows from load-on-scroll appending messages", () => {
        const base = {containerTop: 0, viewportHeight: 800, scrollY: 1600};
        const before = computeProgress({...base, containerHeight: 2000});
        const after = computeProgress({...base, containerHeight: 4000});
        expect(after).toBeLessThan(before);
    });
});
