// This code comes from https://www.ethangunderson.com/sparklines-in-chartjs/

'use strict'
import Chart from 'chart.js/auto';

const CHART_OPTIONS = {
    maintainAspectRatio: false,
    animation: false,
    plugins: {
        legend: {
            display: false,
        },
    },
    scales: {
        x: {
            stacked: true,
            display: true,
            grid: { display: false },
            border: { display: true, color: 'rgba(107, 114, 128, 0.35)' },
            ticks: { display: false },
        },
        y: {
            stacked: true,
            display: false,
            min: 0,
        }
    }
};

/**
 * Creates a minimal sparkline bar chart from pre-fetched trend data.
 * @param {CanvasRenderingContext2D} ctx - Canvas 2D context to render the chart
 * @param {{ successes: number[], errors: number[] }} data - Trend data arrays
 */
export const renderChart = (ctx, data) => {
    const datasets = [
        {
            label: "Success",
            data: data.successes,
            backgroundColor: "#16a34a",
            maxBarThickness: 4,
        },
        {
            label: "Errors",
            data: data.errors,
            backgroundColor: "#dc2626",
            maxBarThickness: 4,
        }
    ];

    // We must specify labels, even if we don't want to display them
    const labels = datasets[0].data.map(() => "");

    return new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: CHART_OPTIONS,
    });
};

/**
 * Creates a minimal sparkline chart by fetching data from a URL.
 * @deprecated Use renderChart with inline data instead to avoid per-row HTTP requests.
 * @param {CanvasRenderingContext2D} ctx - Canvas 2D context to render the chart
 * @param {string} dataUrl - URL endpoint that returns JSON data with a trends object
 */
export const trendsChart = (ctx, dataUrl) => {
    return fetch(dataUrl)
        .then(response => response.json())
        .then(data => renderChart(ctx, data.trends))
        .catch(error => {
            console.error('Error loading chart data:', error);
            return null;
        });
};

export default { renderChart, trendsChart };
