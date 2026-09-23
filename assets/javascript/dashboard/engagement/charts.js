import Chart from "chart.js/auto";
import {ChartManager} from "../charts.js";
import {ordinalRamp, vizToken} from "./viz.js";

// Marks per the dataviz spec: bars capped so the band keeps air, 4px rounded data-end,
// 2px surface gap between stacked segments, 2px lines, >=8px markers.
const MAX_BAR_THICKNESS = 24;
const BAR_RADIUS = 4;
const SURFACE_GAP = 2;
const LINE_WIDTH = 2;
const POINT_RADIUS = 4;
// The API sends ISO dates; Date parses those as UTC midnight, so every format call
// must also read them as UTC or a negative-offset viewer sees the previous day.
const UTC = 'UTC';

function parseIso(iso) {
    return new Date(iso);
}

// "Sep 2025, Oct, Nov, ... Jan 2026, Feb" -- the year appears only when it changes,
// which keeps a 7-bar axis readable without repeating it on every label.
function monthLabels(rows) {
    let previousYear = null;
    return rows.map(row => {
        const parsed = parseIso(row.month);
        const year = parsed.getUTCFullYear();
        const options = year === previousYear
            ? {month: 'short', timeZone: UTC}
            : {month: 'short', year: 'numeric', timeZone: UTC};
        previousYear = year;
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

    lineScales() {
        return {
            x: {
                ...this.defaultOptions.scales.x,
                ticks: {...this.defaultOptions.scales.x.ticks, color: vizToken('--viz-ink-muted')},
                grid: {display: false},
                border: {color: vizToken('--viz-axis')}
            },
            y: {
                ...this.defaultOptions.scales.y,
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

        const lastIndex = data.length - 1;
        // The final month is still in progress, so its closing segment is dashed rather than
        // implying a completed month's value.
        const inProgressSegment = {
            borderDash: context => (context.p1DataIndex === lastIndex ? [5, 4] : undefined)
        };

        const ramp = ordinalRamp();
        const series = [
            {label: '1 week', key: '1_week', color: ramp[0]},
            {label: '2 weeks', key: '2_weeks', color: ramp[1]},
            {label: '3 weeks', key: '3_weeks', color: ramp[2]},
            {label: '4+ weeks', key: '4_plus_weeks', color: ramp[3]}
        ];

        const chartData = {
            labels: monthLabels(data),
            datasets: series.map(({label, key, color}) => ({
                label,
                data: data.map(item => item[key]),
                borderColor: color,
                backgroundColor: color,
                pointBackgroundColor: color,
                pointRadius: POINT_RADIUS,
                pointHoverRadius: POINT_RADIUS + 2,
                pointBorderColor: vizToken('--viz-surface'),
                pointBorderWidth: SURFACE_GAP,
                borderWidth: LINE_WIDTH,
                fill: false,
                tension: 0,
                segment: inProgressSegment
            }))
        };

        this.charts.engagementFrequency = new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: {...this.defaultOptions, plugins: this.chartPlugins(monthTooltipTitle(data)), scales: this.lineScales()}
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
