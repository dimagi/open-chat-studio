/**
 * Evaluation Trend Charts
 * Renders sparkline charts for evaluation run aggregates
 */
import Chart from "chart.js/auto";

const chartInstances = new Map();

document.addEventListener('htmx:beforeSwap', (event) => {
    if (event.detail.target.id === 'trends-container') {
        chartInstances.forEach((chart) => {
            chart.destroy();
        });
        chartInstances.clear();
    }
});

const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
};

/**
 * Creates click and hover handlers for chart point navigation
 */
function createNavigationHandlers(points, buildUrl) {
    if (!buildUrl) return {};
    return {
        onClick: (event, elements) => {
            if (elements.length > 0) {
                const { run_id } = points[elements[0].index];
                window.location.href = buildUrl(run_id);
            }
        },
        onHover: (event, elements) => {
            event.native.target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
        }
    };
}

const colorPalette = {
    primary: '#570df8',
    primaryLight: 'rgba(87, 13, 248, 0.1)',
    categorical: [
        '#570df8', // primary purple
        '#818cf8', // indigo-400
        '#06b6d4', // cyan-500
        '#a78bfa', // violet-400
        '#38bdf8', // sky-400
        '#c4b5fd', // violet-300
        '#7dd3fc', // sky-300
        '#e0e7ff', // indigo-100
    ],
};

/**
 * Render sparkline charts for evaluation trends
 * @param {Object} trendData - Trend data object from the view
 * @param {string} baseUrl - Base URL template for navigating to runs (contains {runId} placeholder)
 */
export function renderTrendCharts(trendData, baseUrl) {
    const buildUrl = baseUrl ? (runId) => baseUrl.replace('{runId}', runId) : null;

    for (const [evaluatorName, fields] of Object.entries(trendData)) {
        for (const [fieldName, fieldData] of Object.entries(fields)) {
            if (fieldData.points.length < 2) continue;

            const canvasId = `chart-${slugify(evaluatorName)}-${slugify(fieldName)}`;
            const canvas = document.getElementById(canvasId);
            if (!canvas) continue;

            if (fieldData.type === 'numeric') {
                renderSparkline(canvas, fieldData, fieldName, buildUrl);
            } else if (fieldData.type === 'categorical') {
                renderStackedBar(canvas, fieldData, fieldName, buildUrl);
            }
        }
    }
}

/**
 * Render a stacked bar chart for categorical data
 */
function renderStackedBar(canvas, fieldData, fieldName, buildUrl) {
    const ctx = canvas.getContext('2d');
    const labels = fieldData.points.map(p => p.date);
    const categories = fieldData.categories || [];

    // Destroy any existing chart on this canvas
    const existingChart = Chart.getChart(canvas);
    if (existingChart) {
        existingChart.destroy();
    }
    chartInstances.delete(canvas.id);

    // Build datasets - one per category
    const datasets = categories.map((category, index) => ({
        label: category,
        data: fieldData.points.map(p => p.distribution?.[category] || 0),
        backgroundColor: colorPalette.categorical[index % colorPalette.categorical.length],
        borderWidth: 0,
        borderSkipped: false,
    }));

    const chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            ...commonOptions,
            indexAxis: 'x',
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#F9FAFB',
                    bodyColor: '#F9FAFB',
                    cornerRadius: 6,
                    padding: 8,
                    callbacks: {
                        title: (items) => items[0].label,
                        label: (item) => `${item.dataset.label}: ${item.raw}%`
                    }
                }
            },
            scales: {
                x: {
                    display: false,
                    stacked: true,
                },
                y: {
                    display: false,
                    stacked: true,
                    max: 100,
                }
            },
            ...createNavigationHandlers(fieldData.points, buildUrl)
        }
    });
    chartInstances.set(canvas.id, chart);
}

/**
 * Render a single sparkline chart
 */
function renderSparkline(canvas, fieldData, fieldName, buildUrl) {
    const ctx = canvas.getContext('2d');
    const values = fieldData.points.map(p => p.value);
    const labels = fieldData.points.map(p => p.date);

    // Destroy any existing chart on this canvas
    const existingChart = Chart.getChart(canvas);
    if (existingChart) {
        existingChart.destroy();
    }
    chartInstances.delete(canvas.id);

    const chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                borderColor: colorPalette.primary,
                backgroundColor: colorPalette.primaryLight,
                fill: true,
                tension: 0.3,
                pointRadius: 2,
                pointHoverRadius: 4,
                borderWidth: 2,
            }]
        },
        options: {
            ...commonOptions,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#F9FAFB',
                    bodyColor: '#F9FAFB',
                    cornerRadius: 6,
                    padding: 8,
                    callbacks: {
                        title: (items) => items[0].label,
                        label: (item) => `${fieldName}: ${item.raw}`
                    }
                }
            },
            scales: {
                x: { display: false },
                y: { display: false }
            },
            ...createNavigationHandlers(fieldData.points, buildUrl)
        }
    });
    chartInstances.set(canvas.id, chart);
}

/**
 * Convert string to URL-friendly slug (matches Django's slugify behavior)
 */
function slugify(str) {
    return str.toLowerCase().replace(/[^a-z0-9_]+/g, '-').replace(/(^-|-$)/g, '');
}

export default { renderTrendCharts };
