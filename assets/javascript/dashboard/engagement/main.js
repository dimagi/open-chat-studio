import TomSelect from "tom-select";
import {percentDelta, sparkline} from './viz.js';

const TOM_SELECT_CONFIG = {
    plugins: ["remove_button", "caret_position"],
    maxItems: null,
    searchField: ['text', 'value'],
    allowEmptyOption: true,
    hideSelected: true,
    closeAfterSelect: true,
    loadThrottle: 200
};

const FILTER_PLACEHOLDERS = {
    experiments: 'All chatbots',
    channels: 'All channels'
};

const FILTER_KEYS = Object.keys(FILTER_PLACEHOLDERS);

const WEEK_BUCKETS = ['1_week', '2_weeks', '3_weeks', '4_plus_weeks'];

function engagementDashboard() {
    return {
        filters: {experiments: [], channels: []},
        summaryStats: [],
        headlineMonth: '',
        hasFrequencyData: true,
        hasNewVsReturningData: true,
        breakdownPanels: [],
        payloads: {summary: null, avgDuration: null, frequency: null, newVsReturning: null},
        loadingStates: {summary: false, frequency: false, newVsReturning: false, chatbot: false, channel: false},
        endpoints: {summary: '', frequency: '', newVsReturning: '', sessionDuration: '', breakdownChatbot: '', breakdownChannel: ''},

        init() {
            this.loadEndpoints();
            this.setupTomSelect();
            this.watchTheme();
            this.refreshAll();
        },

        loadEndpoints() {
            // This page is nested one segment deeper than its API endpoints, so a relative fetch would resolve wrong.
            const config = document.getElementById('engagementDashboardConfig').dataset;
            this.endpoints = {
                summary: config.summaryUrl,
                frequency: config.frequencyUrl,
                newVsReturning: config.newVsReturningUrl,
                sessionDuration: config.sessionDurationUrl,
                breakdownChatbot: config.breakdownChatbotUrl,
                breakdownChannel: config.breakdownChannelUrl
            };
        },

        setupTomSelect() {
            FILTER_KEYS.forEach(key => {
                const element = document.getElementById(`id_${key}`);
                if (!element || element.tomselect) return;
                new TomSelect(element, {...TOM_SELECT_CONFIG, placeholder: FILTER_PLACEHOLDERS[key]});
            });
        },

        handleFilterChange() {
            const form = document.getElementById('engagementFilterForm');
            if (!form) return;
            const formData = new FormData(form);
            const filters = {experiments: [], channels: []};
            for (const [key, value] of formData.entries()) {
                if (filters[key]) filters[key].push(value);
            }
            this.filters = filters;
            this.refreshAll();
        },

        buildParams() {
            const params = new URLSearchParams();
            for (const [key, values] of Object.entries(this.filters)) {
                values.forEach(value => params.append(key, value));
            }
            return params;
        },

        async apiRequest(endpoint) {
            const response = await fetch(`${endpoint}?${this.buildParams()}`);
            if (!response.ok) {
                throw new Error(`Request to ${endpoint} failed: ${response.status}`);
            }
            return response.json();
        },

        async refreshAll() {
            await Promise.all([
                this.loadSummary(),
                this.loadFrequency(),
                this.loadNewVsReturning(),
                this.loadBreakdowns()
            ]);
        },

        watchTheme() {
            // Chart and sparkline colours are read from CSS custom properties at build time,
            // so a theme change has to redraw them rather than just restyle the page.
            const observer = new MutationObserver(() => {
                this.renderSummary();
                this.renderFrequency();
                this.renderNewVsReturning();
            });
            observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
        },

        async loadSummary() {
            this.loadingStates.summary = true;
            try {
                const [summary, avgDuration] = await Promise.all([
                    this.apiRequest(this.endpoints.summary),
                    this.apiRequest(this.endpoints.sessionDuration)
                ]);
                this.payloads.summary = summary;
                this.payloads.avgDuration = avgDuration;
                this.renderSummary();
            } catch (error) {
                console.error('Failed to load engagement summary:', error);
            } finally {
                this.loadingStates.summary = false;
            }
        },

        renderSummary() {
            const summary = this.payloads.summary;
            if (!summary) return;
            const avgDuration = this.payloads.avgDuration || [];

            // The headline is the last complete month. A partial month cannot be compared with a
            // whole one, and Core Users Rate reads near zero in a month's first week because
            // nobody can yet have been active in two of its weeks.
            const closed = summary.slice(0, -1);
            const partial = summary[summary.length - 1] || {};
            const headline = closed[closed.length - 1] || {};
            const closedDuration = avgDuration.slice(0, -1);
            const partialDuration = avgDuration[avgDuration.length - 1] || {};
            const headlineDuration = closedDuration[closedDuration.length - 1] || {};

            const monthName = iso => (iso
                ? new Date(iso).toLocaleDateString(undefined, {month: 'long', year: 'numeric', timeZone: 'UTC'})
                : '');
            const periodLabel = monthName(headline.month);
            this.headlineMonth = periodLabel;

            const today = new Date();
            const partialStart = partial.month ? new Date(partial.month) : null;
            const partialRange = partialStart
                ? `${partialStart.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'})}\u2013${today.getUTCDate()}`
                : '';
            const secondaryTitle = `Month to date, ${monthName(partial.month)}. Not comparable with a complete month.`;

            const mauSeries = closed.map(row => row.mau || 0);
            const coreSeries = closed.map(row => row.core_users_rate || 0);
            const durationSeries = closedDuration.map(row => row.minutes || 0);

            this.summaryStats = [
                {
                    label: 'Monthly Active Users',
                    tooltip: 'Participants who sent at least one message in the month. The headline is the last complete month; the line shows the 6 months before it.',
                    value: headline.mau || 0,
                    periodLabel,
                    delta: percentDelta(mauSeries),
                    secondary: `${partialRange}: ${partial.mau || 0}`,
                    secondaryTitle,
                    sparkline: sparkline(mauSeries),
                    sparklineLabel: 'Monthly active users over the last 6 complete months'
                },
                {
                    label: 'Core Users Rate',
                    tooltip: 'The share of the month\u2019s participants who were active in two or more different weeks. The headline is the last complete month; the line shows the 6 months before it.',
                    value: `${(headline.core_users_rate || 0).toFixed(1)}%`,
                    periodLabel,
                    delta: percentDelta(coreSeries),
                    secondary: `${partialRange}: ${(partial.core_users_rate || 0).toFixed(1)}%`,
                    secondaryTitle,
                    sparkline: sparkline(coreSeries),
                    sparklineLabel: 'Core users rate over the last 6 complete months'
                },
                {
                    label: 'Average Session Duration',
                    tooltip: 'Average length of a chat session, counting only sessions that have ended. The headline is the last complete month; the line shows the 6 months before it.',
                    value: `${(headlineDuration.minutes || 0).toFixed(1)} min`,
                    periodLabel,
                    delta: percentDelta(durationSeries),
                    secondary: `${partialRange}: ${(partialDuration.minutes || 0).toFixed(1)} min`,
                    secondaryTitle,
                    sparkline: sparkline(durationSeries),
                    sparklineLabel: 'Average session duration over the last 6 complete months'
                }
            ];
        },

        async loadFrequency() {
            this.loadingStates.frequency = true;
            try {
                this.payloads.frequency = await this.apiRequest(this.endpoints.frequency);
                this.renderFrequency();
            } catch (error) {
                console.error('Failed to load engagement frequency:', error);
            } finally {
                this.loadingStates.frequency = false;
            }
        },

        renderFrequency() {
            const data = this.payloads.frequency;
            if (!data) return;
            this.hasFrequencyData = data.some(row => WEEK_BUCKETS.some(bucket => row[bucket] > 0));
            if (this.hasFrequencyData) {
                this.$nextTick(() => window.engagementChartManager.renderEngagementFrequencyChart(data));
            } else {
                window.engagementChartManager.destroyChart('engagementFrequency');
            }
        },

        async loadNewVsReturning() {
            this.loadingStates.newVsReturning = true;
            try {
                this.payloads.newVsReturning = await this.apiRequest(this.endpoints.newVsReturning);
                this.renderNewVsReturning();
            } catch (error) {
                console.error('Failed to load new-vs-returning data:', error);
            } finally {
                this.loadingStates.newVsReturning = false;
            }
        },

        renderNewVsReturning() {
            const data = this.payloads.newVsReturning;
            if (!data) return;
            this.hasNewVsReturningData = data.some(row => row.new > 0 || row.returning > 0);
            if (this.hasNewVsReturningData) {
                this.$nextTick(() => window.engagementChartManager.renderNewVsReturningChart(data));
            } else {
                window.engagementChartManager.destroyChart('newVsReturning');
            }
        },

        async loadBreakdowns() {
            const panels = [
                {
                    key: 'chatbot', title: 'Top chatbots', dimensionLabel: 'Chatbot',
                    endpoint: this.endpoints.breakdownChatbot
                },
                {
                    key: 'channel', title: 'Engagement by channel', dimensionLabel: 'Channel',
                    endpoint: this.endpoints.breakdownChannel
                }
            ];

            if (this.breakdownPanels.length !== panels.length) {
                this.breakdownPanels = panels.map(panel => ({...panel, rows: []}));
            }

            await Promise.all(panels.map(async (panel, index) => {
                this.loadingStates[panel.key] = true;
                try {
                    const rows = await this.apiRequest(panel.endpoint);
                    this.breakdownPanels[index] = {
                        ...panel,
                        rows: rows.map(row => ({
                            ...row,
                            delta: percentDelta([row.previous, row.participants])
                        }))
                    };
                } catch (error) {
                    console.error(`Failed to load ${panel.key} breakdown:`, error);
                } finally {
                    this.loadingStates[panel.key] = false;
                }
            }));
        }
    };
}

window.engagementDashboard = engagementDashboard;
