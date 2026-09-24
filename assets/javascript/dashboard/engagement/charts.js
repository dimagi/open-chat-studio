import Chart from "chart.js/auto";
import {ChartManager} from "../charts.js";
import {ordinalRamp, vizToken} from "./viz.js";

// Marks per the dataviz spec: bars capped so the band keeps air, 4px rounded data-end,
// 2px surface gap between stacked segments, 2px lines, >=8px markers.
const MAX_BAR_THICKNESS = 24;
const BAR_RADIUS = 4;
const SURFACE_GAP = 2;
// The API sends ISO dates; Date parses those as UTC midnight, so every format call
// must also read them as UTC or a negative-offset viewer sees the previous day.
const UTC = 'UTC';

function parseIso(iso) {
    return new Date(iso);
}

// "Sep 2025, Oct, Nov, ... Jan 2026, Feb" -- the year appears only when it changes,
// which keeps a 7-bar axis readable without repeating it on every label.
function monthLabels(rows, {markPartial = false} = {}) {
    let previousYear = null;
    return rows.map((row, index) => {
        const parsed = parseIso(row.month);
        const year = parsed.getUTCFullYear();
        const options = year === previousYear
            ? {month: 'short', timeZone: UTC}
            : {month: 'short', year: 'numeric', timeZone: UTC};
        previousYear = year;
        // The newest month is still filling. Labelling it with the day the data runs to
        // ("Sep 24") says that concretely, where a shorter bar alone would read as a decline.
        // UTC, because the service buckets in UTC -- local time could name the wrong day.
        if (markPartial && row.in_progress && index === rows.length - 1) {
            const month = parsed.toLocaleDateString(undefined, {month: 'short', timeZone: UTC});
            return `${month} ${new Date().getUTCDate()}`;
        }
        return parsed.toLocaleDateString(undefined, options);
    });
}

function weekLabels(rows) {
    return rows.map(row => parseIso(row.week).toLocaleDateString(undefined, {day: 'numeric', month: 'short', timeZone: UTC}));
}

function monthTooltipTitle(rows) {
    return items => parseIso(rows[items[0].dataIndex].month)
        .toLocaleDateString(undefined, {month: 'long', year: 'numeric', timeZone: UTC});
}

function weekTooltipTitle(rows) {
    return items => {
        const start = parseIso(rows[items[0].dataIndex].week);
        const end = new Date(start.getTime() + 6 * 24 * 60 * 60 * 1000);
        const startText = start.toLocaleDateString(undefined, {day: 'numeric', month: 'short', timeZone: UTC});
        const endText = end.toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric', timeZone: UTC});
        return `${startText} \u2013 ${endText}`;
    };
}

class EngagementChartManager extends ChartManager {
    stackedScales() {
        return {
            x: {
                ...this.defaultOptions.scales.x,
                stacked: true,
                ticks: {...this.defaultOptions.scales.x.ticks, color: vizToken('--viz-ink-muted')},
                grid: {display: false},
                border: {color: vizToken('--viz-axis')}
            },
            y: {
                ...this.defaultOptions.scales.y,
                stacked: true,
                beginAtZero: true,
                ticks: {...this.defaultOptions.scales.y.ticks, precision: 0, color: vizToken('--viz-ink-muted')},
                grid: {color: vizToken('--viz-grid'), drawTicks: false},
                border: {display: false}
            }
        };
    }

    chartPlugins(tooltipTitle) {
        return {
            ...this.defaultOptions.plugins,
            legend: {
                ...this.defaultOptions.plugins.legend,
                labels: {...this.defaultOptions.plugins.legend.labels, color: vizToken('--viz-ink-muted'), boxWidth: 8, boxHeight: 8}
            },
            tooltip: {
                ...this.defaultOptions.plugins.tooltip,
                callbacks: {title: tooltipTitle}
            }
        };
    }

    renderEngagementFrequencyChart(data) {
        const ctx = document.getElementById('engagementFrequencyChart');
        if (!ctx) return;
        this.destroyChart('engagementFrequency');

        // Part-to-whole: the four buckets partition that month's active participants, so the
        // stack height is MAU and each segment is a cohort. The ordinal ramp reads correctly
        // here because filled segments give it real area to work with.
        const ramp = ordinalRamp();
        const series = [
            {label: '1 week', key: '1_week', color: ramp[0]},
            {label: '2 weeks', key: '2_weeks', color: ramp[1]},
            {label: '3 weeks', key: '3_weeks', color: ramp[2]},
            {label: '4+ weeks', key: '4_plus_weeks', color: ramp[3]}
        ];

        const chartData = {
            labels: monthLabels(data, {markPartial: true}),
            datasets: series.map(({label, key, color}) => ({
                label,
                data: data.map(item => item[key]),
                backgroundColor: color,
                maxBarThickness: MAX_BAR_THICKNESS,
                borderColor: vizToken('--viz-surface'),
                borderWidth: {top: SURFACE_GAP},
                borderRadius: BAR_RADIUS
            }))
        };

        this.charts.engagementFrequency = new Chart(ctx, {
            type: 'bar',
            data: chartData,
            options: {
                ...this.defaultOptions,
                plugins: this.chartPlugins(monthTooltipTitle(data)),
                scales: this.stackedScales()
            }
        });
    }

    renderNewVsReturningChart(data) {
        const ctx = document.getElementById('newVsReturningChart');
        if (!ctx) return;
        this.destroyChart('newVsReturning');

        const chartData = {
            labels: weekLabels(data),
            datasets: [
                {
                    label: 'New',
                    data: data.map(item => item.new),
                    backgroundColor: vizToken('--viz-series-new'),
                    maxBarThickness: MAX_BAR_THICKNESS,
                    borderColor: vizToken('--viz-surface'),
                    borderWidth: {top: SURFACE_GAP},
                    borderRadius: BAR_RADIUS
                },
                {
                    label: 'Returning',
                    data: data.map(item => item.returning),
                    backgroundColor: vizToken('--viz-series-returning'),
                    maxBarThickness: MAX_BAR_THICKNESS,
                    borderColor: vizToken('--viz-surface'),
                    borderWidth: {top: SURFACE_GAP},
                    borderRadius: BAR_RADIUS
                }
            ]
        };

        this.charts.newVsReturning = new Chart(ctx, {
            type: 'bar',
            data: chartData,
            options: {...this.defaultOptions, plugins: this.chartPlugins(weekTooltipTitle(data)), scales: this.stackedScales()}
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.engagementChartManager = new EngagementChartManager();
});
