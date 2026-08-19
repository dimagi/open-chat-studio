import {describe, expect, it} from "vitest";

import {SERVICE_KINDS, formatCost, providerTotals, serviceKindSeries} from "./costBreakdown.js";

describe("providerTotals", () => {
    it("sums model rows per provider, sorted by descending cost", () => {
        const rows = [
            {provider_type: "openai", model_name: "gpt-4o-mini", cost: 1.0},
            {provider_type: "anthropic", model_name: "claude-sonnet-5", cost: 4.0},
            {provider_type: "openai", model_name: "gpt-4o", cost: 2.5},
        ];

        expect(providerTotals(rows)).toEqual([
            {provider: "anthropic", cost: 4.0},
            {provider: "openai", cost: 3.5},
        ]);
    });

    it("returns an empty list for missing input", () => {
        expect(providerTotals(undefined)).toEqual([]);
    });
});

describe("serviceKindSeries", () => {
    const rows = [
        {service_kind: "llm_input", cost: 1.0, tokens: 1000},
        {service_kind: "llm_output", cost: 2.0, tokens: 500},
    ];

    it("zero-fills absent kinds over the fixed four-kind order", () => {
        expect(serviceKindSeries(rows, "cost")).toEqual({
            labels: ["Fresh input", "Cached input", "Cache write", "Output"],
            values: [1.0, 0, 0, 2.0],
        });
    });

    it("switches to token counts in tokens mode", () => {
        expect(serviceKindSeries(rows, "tokens").values).toEqual([1000, 0, 0, 500]);
    });

    it("handles missing input", () => {
        expect(serviceKindSeries(undefined, "cost").values).toEqual([0, 0, 0, 0]);
    });
});

describe("formatCost", () => {
    it("uses 2 decimals at a cent or more", () => {
        expect(formatCost(1.5)).toBe("$1.50");
    });

    it("uses 4 decimals below a cent so sub-cent spend does not flatten to $0.00", () => {
        expect(formatCost(0.0042)).toBe("$0.0042");
    });

    it("renders zero and missing values as $0.00", () => {
        expect(formatCost(0)).toBe("$0.00");
        expect(formatCost(undefined)).toBe("$0.00");
    });
});

describe("SERVICE_KINDS", () => {
    it("covers the four ServiceKind values in display order", () => {
        expect(SERVICE_KINDS.map(kind => kind.key)).toEqual([
            "llm_input",
            "llm_cached_input",
            "llm_cache_write",
            "llm_output",
        ]);
    });
});
