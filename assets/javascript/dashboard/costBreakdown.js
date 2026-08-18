/**
 * Data shaping for the cost panel's breakdown charts. Pure functions so the
 * grouping/formatting rules are unit-testable apart from Chart.js rendering.
 */

// Display order and labels for the four UsageRecord.service_kind values.
export const SERVICE_KINDS = [
    {key: "llm_input", label: "Fresh input"},
    {key: "llm_cached_input", label: "Cached input"},
    {key: "llm_cache_write", label: "Cache write"},
    {key: "llm_output", label: "Output"},
];

export function providerTotals(byModel) {
    const totals = new Map();
    (byModel || []).forEach(row => {
        totals.set(row.provider_type, (totals.get(row.provider_type) || 0) + (row.cost || 0));
    });
    return [...totals.entries()]
        .map(([provider, cost]) => ({provider, cost}))
        .sort((a, b) => b.cost - a.cost);
}

export function serviceKindSeries(byServiceKind, mode) {
    const rows = new Map((byServiceKind || []).map(row => [row.service_kind, row]));
    const field = mode === "tokens" ? "tokens" : "cost";
    return {
        labels: SERVICE_KINDS.map(kind => kind.label),
        values: SERVICE_KINDS.map(kind => (rows.get(kind.key) || {})[field] || 0),
    };
}

// Mirrors the server-side `cost_display` filter (apps/cost_tracking/templatetags):
// 4 decimals below $0.01 so sub-cent spend doesn't flatten to $0.00.
export function formatCost(value) {
    const num = value || 0;
    const decimals = num !== 0 && num < 0.01 ? 4 : 2;
    return `$${num.toFixed(decimals)}`;
}
