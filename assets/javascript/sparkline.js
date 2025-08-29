// This code comes from https://www.ethangunderson.com/sparklines-in-chartjs/

'use strict'
import Chart from 'chart.js/auto';

/**
 * Creates a minimal sparkline chart by fetching data from a URL
 * @param {HTMLCanvasElement} ctx - Canvas element to render the chart
 * @param {string} dataUrl - URL endpoint that returns JSON data with a 'data' array
 */
export const sparklineChart = (ctx, dataUrl) => {
    let seriesData;

    fetch(dataUrl)
        .then(response => response.json())
        .then(data => {
            seriesData = data.data;
            return new Chart(ctx, {
                type: "line",
                data: {
                    labels: seriesData,
                    datasets: [
                        {
                            data: seriesData,
                            fill: false,
                            pointRadius: 0,
                            spanGaps: true,
                            tension: 0.2,
                            borderColor: '#3B82F6',
                            borderWidth: 1.5
                        },
                    ],
                },
                options: {
                    events: [],
                    responsive: false,
                    plugins: {
                        legend: {
                            display: false,
                            labels: {
                                display: false
                            }
                        },
                        tooltip: {
                            enabled: false
                        }
                    },
                    scales: {
                        x: {
                            display: false,
                        },
                        y: {
                            display: false,
                        }
                    },
                },
            })
        });
    };

export default { sparklineChart };

