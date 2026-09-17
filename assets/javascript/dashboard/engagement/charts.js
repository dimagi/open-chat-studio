import Chart from "chart.js/auto";
import {ChartManager} from "../charts.js";

const IN_PROGRESS_ALPHA = "4D";

function withInProgressAlpha(color, count) {
    return Array.from({length: count}, (_, index) => (index === count - 1 ? color + IN_PROGRESS_ALPHA : color));
}

class EngagementChartManager extends ChartManager {
    renderEngagementFrequencyChart(data) {
        const ctx = document.getElementById('engagementFrequencyChart');
        if (!ctx) return;
        this.destroyChart('engagementFrequency');

        const labels = data.map(item => item.month);
        const count = data.length;

        const chartData = {
            labels,
            datasets: [
                {label: '1 week', data: data.map(i => i['1_week']), backgroundColor: withInProgressAlpha(this.colorPalette.info, count)},
                {label: '2 weeks', data: data.map(i => i['2_weeks']), backgroundColor: withInProgressAlpha(this.colorPalette.primary, count)},
                {label: '3 weeks', data: data.map(i => i['3_weeks']), backgroundColor: withInProgressAlpha(this.colorPalette.secondary, count)},
                {label: '4+ weeks', data: data.map(i => i['4_plus_weeks']), backgroundColor: withInProgressAlpha(this.colorPalette.success, count)}
            ]
        };

        this.charts.engagementFrequency = new Chart(ctx, {
            type: 'bar',
            data: chartData,
            options: {
                ...this.defaultOptions,
                scales: {
                    x: {...this.defaultOptions.scales.x, stacked: true},
                    y: {...this.defaultOptions.scales.y, stacked: true, title: {display: true, text: 'Participants'}}
                }
            }
        });
    }

    renderNewVsReturningChart(data) {
        const ctx = document.getElementById('newVsReturningChart');
        if (!ctx) return;
        this.destroyChart('newVsReturning');

        const chartData = {
            labels: data.map(item => item.week),
            datasets: [
                {label: 'New', data: data.map(item => item.new), backgroundColor: this.colorPalette.success},
                {label: 'Returning', data: data.map(item => item.returning), backgroundColor: this.colorPalette.info}
            ]
        };

        this.charts.newVsReturning = new Chart(ctx, {
            type: 'bar',
            data: chartData,
            options: {
                ...this.defaultOptions,
                scales: {
                    x: {...this.defaultOptions.scales.x, stacked: true},
                    y: {...this.defaultOptions.scales.y, stacked: true, title: {display: true, text: 'Participants'}}
                }
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.engagementChartManager = new EngagementChartManager();
});
