// This code comes from https://www.ethangunderson.com/sparklines-in-chartjs/

'use strict'
import Chart from 'chart.js/auto';

export const sparklineChart = (ctx, seriesData) => {
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
};

export default { sparklineChart };

