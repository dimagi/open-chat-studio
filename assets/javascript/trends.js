// This code comes from https://www.ethangunderson.com/sparklines-in-chartjs/

'use strict'
import Chart from 'chart.js/auto';

const CHART_OPTIONS = {
    plugins: {
        legend: {
            display: false,
            labels: {
                display: false
            }
        },
    },
    responsive: true,
    scales: {
        x: {
            stacked: true,
            display: false,
        },
        y: {
            stacked: true,
            display: false
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
            backgroundColor: "green",
            barThickness: 1,
        },
        {
            label: "Errors",
            data: data.errors,
            backgroundColor: "red",
            barThickness: 1,
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
