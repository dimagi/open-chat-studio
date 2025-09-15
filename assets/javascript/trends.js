// This code comes from https://www.ethangunderson.com/sparklines-in-chartjs/

'use strict'
import Chart from 'chart.js/auto';

/**
 * Creates a minimal sparkline chart by fetching data from a URL
 * @param {HTMLCanvasElement} ctx - Canvas element to render the chart
 * @param {string} dataUrl - URL endpoint that returns JSON data with a 'data' array
 */
export const trendsChart = (ctx, dataUrl) => {
    return fetch(dataUrl)
        .then(response => response.json())
        .then(data => {
            let datasets = [
                {
                    label: "Success",
                    data: data.trends.successes,
                    backgroundColor: "green",
                    barThickness: 1,
                },
                {
                    label: "Errors",
                    data: data.trends.errors,
                    backgroundColor: "red",
                    barThickness: 1,
                }
            ]

            // We must specify labels, even if we don't want to display them
            const labels = datasets[0].data.map(() => "");
            
            return new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: datasets
                },
                options: {
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
                }
            });
        })
        .catch(error => {
            console.error('Error loading chart data:', error);
            return null;
        });
};

export default { trendsChart };

