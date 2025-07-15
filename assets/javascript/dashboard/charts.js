/**
 * Dashboard Charts JavaScript
 * Handles Chart.js chart creation and management
 */
import Chart from "chart.js/auto";

class ChartManager {
    constructor() {
        this.charts = {};
        this.colorPalette = {
            primary: '#3B82F6',
            secondary: '#8B5CF6',
            success: '#10B981',
            warning: '#F59E0B',
            danger: '#EF4444',
            info: '#06B6D4',
            light: '#F3F4F6',
            dark: '#374151'
        };
        
        this.setupChartDefaults();
    }
    
    setupChartDefaults() {
        Chart.defaults.font.family = '"Inter", sans-serif';
        Chart.defaults.font.size = 12;
        Chart.defaults.color = '#6B7280';
        
        // Default chart options
        this.defaultOptions = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        usePointStyle: true,
                        padding: 20
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#F9FAFB',
                    bodyColor: '#F9FAFB',
                    borderColor: '#374151',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        maxTicksLimit: 8
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(156, 163, 175, 0.1)'
                    }
                }
            }
        };
    }
    
    renderActiveParticipantsChart(data) {
        const ctx = document.getElementById('activeParticipantsChart');
        if (!ctx) return;
        
        this.destroyChart('activeParticipants');
        
        const chartData = {
            labels: data.map(item => this.formatDateLabel(item.date)),
            datasets: [{
                label: 'Active Participants',
                data: data.map(item => item.active_participants),
                borderColor: this.colorPalette.primary,
                backgroundColor: this.colorPalette.primary + '20',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        };
        
        const options = {
            ...this.defaultOptions,
            plugins: {
                ...this.defaultOptions.plugins,
                title: {
                    display: false
                }
            },
            scales: {
                ...this.defaultOptions.scales,
                y: {
                    ...this.defaultOptions.scales.y,
                    title: {
                        display: true,
                        text: 'Number of Participants'
                    }
                }
            }
        };
        
        this.charts.activeParticipants = new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: options
        });
    }
    
    renderSessionAnalyticsChart(data) {
        const ctx = document.getElementById('sessionAnalyticsChart');
        if (!ctx) return;
        
        this.destroyChart('sessionAnalytics');

        const chartData = {
            labels: data.map(item => this.formatDateLabel(item.date)),
            datasets: [{
                label: 'Active Sessions',
                data: data.map(item => item.active_sessions),
                borderColor: this.colorPalette.secondary,
                backgroundColor: this.colorPalette.secondary + '20',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        };

        const options = {
            ...this.defaultOptions,
            plugins: {
                ...this.defaultOptions.plugins,
                title: {
                    display: false
                }
            },
            scales: {
                ...this.defaultOptions.scales,
                y: {
                    ...this.defaultOptions.scales.y,
                    title: {
                        display: true,
                        text: 'Number of Sessions'
                    }
                }
            }
        };

        this.charts.sessionAnalytics = new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: options
        });
    }
    
    renderMessageVolumeChart(data) {
        const ctx = document.getElementById('messageVolumeChart');
        if (!ctx) return;
        
        this.destroyChart('messageVolume');
        
        const labels = data.totals?.map(item => this.formatDateLabel(item.date)) || [];
        
        const chartData = {
            labels: labels,
            datasets: [
                {
                    label: 'Human Messages',
                    data: data.totals?.map(item => item.human_messages) || [],
                    borderColor: this.colorPalette.success,
                    backgroundColor: this.colorPalette.success + '80',
                    fill: false,
                    tension: 0.3
                },
                {
                    label: 'AI Messages',
                    data: data.totals?.map(item => item.ai_messages) || [],
                    borderColor: this.colorPalette.info,
                    backgroundColor: this.colorPalette.info + '80',
                    fill: false,
                    tension: 0.3
                }
            ]
        };
        
        const options = {
            ...this.defaultOptions,
            plugins: {
                ...this.defaultOptions.plugins,
                title: {
                    display: false
                }
            },
            scales: {
                ...this.defaultOptions.scales,
                y: {
                    ...this.defaultOptions.scales.y,
                    title: {
                        display: true,
                        text: 'Number of Messages'
                    }
                }
            }
        };
        
        this.charts.messageVolume = new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: options
        });
    }

    renderAverageResponseTimeChart(data) {
        const ctx = document.getElementById('averageResponseTimeChart');
        if (!ctx) return;

        this.destroyChart('averageResponseTime');

        const labels = data?.map(item => this.formatDateLabel(item.date)) || [];

        const chartData = {
            labels: labels,
            datasets: [
                {
                    label: 'Average Response Time (sec)',
                    data: data?.map(item => item.avg_response_time_sec) || [],
                    borderColor: this.colorPalette.warning,
                    backgroundColor: this.colorPalette.warning + '80',
                    fill: false,
                    tension: 0.3
                }
            ]
        };

        const options = {
            ...this.defaultOptions,
            plugins: {
                ...this.defaultOptions.plugins,
                title: {
                    display: true,
                    text: 'Average Response Time'
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            const sec = context.parsed.y || 0;
                            return `Avg Response Time: ${sec.toFixed(2)} sec`;
                        }
                    }
                }
            },
            scales: {
                ...this.defaultOptions.scales,
                y: {
                    ...this.defaultOptions.scales.y,
                    title: {
                        display: true,
                        text: 'Seconds'
                    },
                    beginAtZero: true
                }
            }
        };

        this.charts.averageResponseTime = new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: options
        });
    }
    
    renderChannelBreakdownChart(data) {
        const ctx = document.getElementById('channelBreakdownChart');
        if (!ctx) return;
        
        this.destroyChart('channelBreakdown');
        
        const channels = data.platforms || [];
        const totalSessions = data.totals?.sessions || 1;
        
        const chartData = {
            labels: channels.map(channel => channel.platform || 'Unknown'),
            datasets: [{
                data: channels.map(channel => channel.sessions),
                backgroundColor: [
                    this.colorPalette.primary,
                    this.colorPalette.secondary,
                    this.colorPalette.success,
                    this.colorPalette.warning,
                    this.colorPalette.danger,
                    this.colorPalette.info
                ].slice(0, channels.length),
                borderWidth: 2,
                borderColor: '#ffffff'
            }]
        };
        
        const options = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        usePointStyle: true,
                        padding: 15,
                        generateLabels: function(chart) {
                            const data = chart.data;
                            if (data.labels.length && data.datasets.length) {
                                return data.labels.map((label, i) => {
                                    const dataset = data.datasets[0];
                                    const value = dataset.data[i];
                                    const percentage = ((value / totalSessions) * 100).toFixed(1);
                                    return {
                                        text: `${label} (${percentage}%)`,
                                        fillStyle: dataset.backgroundColor[i],
                                        hidden: false,
                                        index: i
                                    };
                                });
                            }
                            return [];
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed;
                            const percentage = ((value / totalSessions) * 100).toFixed(1);
                            return `${label}: ${value} sessions (${percentage}%)`;
                        }
                    }
                }
            }
        };
        
        this.charts.channelBreakdown = new Chart(ctx, {
            type: 'doughnut',
            data: chartData,
            options: options
        });
    }
    
    renderSessionLengthChart(distributionData) {
        const ctx = document.getElementById('sessionLengthChart');
        if (!ctx) return;
        
        this.destroyChart('sessionLength');
        
        const chartData = {
            labels: distributionData.map(bin => bin.label),
            datasets: [{
                label: 'Sessions',
                data: distributionData.map(bin => bin.count),
                backgroundColor: this.colorPalette.primary + '80',
                borderColor: this.colorPalette.primary,
                borderWidth: 1
            }]
        };
        
        const options = {
            ...this.defaultOptions,
            plugins: {
                ...this.defaultOptions.plugins,
                legend: {
                    display: false
                }
            },
            scales: {
                ...this.defaultOptions.scales,
                x: {
                    ...this.defaultOptions.scales.x,
                    title: {
                        display: true,
                        text: 'Session Duration'
                    }
                },
                y: {
                    ...this.defaultOptions.scales.y,
                    title: {
                        display: true,
                        text: 'Number of Sessions'
                    }
                }
            }
        };
        
        this.charts.sessionLength = new Chart(ctx, {
            type: 'bar',
            data: chartData,
            options: options
        });
    }
    
    destroyChart(chartKey) {
        if (this.charts[chartKey]) {
            this.charts[chartKey].destroy();
            delete this.charts[chartKey];
        }
    }
    
    destroyAllCharts() {
        Object.keys(this.charts).forEach(key => {
            this.destroyChart(key);
        });
    }
    
    formatDateLabel(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diffTime = Math.abs(now - date);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        
        if (diffDays <= 7) {
            // Show day of week for recent dates
            return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        } else if (diffDays <= 365) {
            // Show month and day for dates within a year
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } else {
            // Show month and year for older dates
            return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
        }
    }
    
    updateChartTheme(isDarkMode) {
        const textColor = isDarkMode ? '#F3F4F6' : '#374151';
        const gridColor = isDarkMode ? 'rgba(156, 163, 175, 0.2)' : 'rgba(156, 163, 175, 0.1)';
        
        Chart.defaults.color = textColor;
        
        Object.values(this.charts).forEach(chart => {
            if (chart.options.scales) {
                if (chart.options.scales.x) {
                    chart.options.scales.x.ticks.color = textColor;
                    chart.options.scales.x.grid.color = gridColor;
                }
                if (chart.options.scales.y) {
                    chart.options.scales.y.ticks.color = textColor;
                    chart.options.scales.y.grid.color = gridColor;
                }
                if (chart.options.scales.y1) {
                    chart.options.scales.y1.ticks.color = textColor;
                }
            }
            
            if (chart.options.plugins?.legend?.labels) {
                chart.options.plugins.legend.labels.color = textColor;
            }
            
            chart.update('none');
        });
    }
}

// Initialize chart manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.chartManager = new ChartManager();
    
    // Listen for theme changes
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'data-theme') {
                const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';
                window.chartManager.updateChartTheme(isDarkMode);
            }
        });
    });
    
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme']
    });
});
