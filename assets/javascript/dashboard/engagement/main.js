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
    channels: 'All channels',
    participants: 'All participants',
    tags: 'All tags'
};

const FILTER_KEYS = Object.keys(FILTER_PLACEHOLDERS);

const WEEK_BUCKETS = ['1_week', '2_weeks', '3_weeks', '4_plus_weeks'];

function engagementDashboard() {
    return {
        filters: {experiments: [], channels: [], participants: [], tags: []},
        summaryStats: [],
        hasFrequencyData: true,
        hasNewVsReturningData: true,
        breakdownPanels: [],
        loadingStates: {summary: false, frequency: false, newVsReturning: false, chatbot: false, channel: false},
        endpoints: {summary: '', frequency: '', newVsReturning: '', sessionDuration: '', breakdownChatbot: '', breakdownChannel: ''},
        links: {chatbots: '', sessions: ''},

        init() {
            this.loadEndpoints();
            this.setupTomSelect();
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
            this.links = {chatbots: config.chatbotsUrl, sessions: config.sessionsUrl};
        },

        setupTomSelect() {
            FILTER_KEYS.forEach(key => {
                const element = document.getElementById(`id_${key}`);
                if (!element || element.tomselect) return;
                new TomSelect(element, {
                    ...TOM_SELECT_CONFIG,
                    placeholder: FILTER_PLACEHOLDERS[key],
                    onChange: () => this.handleFilterChange()
                });
            });
        },

        handleFilterChange() {
            const form = document.getElementById('engagementFilterForm');
            if (!form) return;
            const formData = new FormData(form);
            const filters = {experiments: [], channels: [], participants: [], tags: []};
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

        async loadSummary() {
            this.loadingStates.summary = true;
            try {
                const [summary, avgDuration] = await Promise.all([
                    this.apiRequest(this.endpoints.summary),
                    this.apiRequest(this.endpoints.sessionDuration)
                ]);
                const latest = summary[summary.length - 1] || {};
                const inProgress = latest.in_progress || false;

                // The badge names the span the number actually covers, so a partial month is
                // never mistaken for a full one.
                const monthStart = latest.month ? new Date(latest.month) : null;
                const today = new Date();
                const monthName = monthStart
                    ? monthStart.toLocaleDateString(undefined, {month: 'long', year: 'numeric', timeZone: 'UTC'})
                    : '';
                const partialRange = monthStart
                    ? `${monthStart.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'})}\u2013${today.getDate()}`
                    : '';
                const partialRangeTitle = `${monthName} so far \u2014 this month is not over, so the value covers a partial month`;
                const partialRangeCaption = partialRange ? `${partialRange} so far` : '';

                const mauSeries = summary.map(row => row.mau || 0);
                const coreSeries = summary.map(row => row.core_users_rate || 0);
                const durationSeries = (avgDuration || []).map(row => row.minutes || 0);
                const latestDuration = durationSeries[durationSeries.length - 1] || 0;

                // A delta is only honest between two closed months, so it is withheld while the
                // newest month is still filling; the tile shows the in-progress badge instead.
                const deltaFor = (series, options) => (inProgress ? null : percentDelta(series, options));

                this.summaryStats = [
                    {
                        label: 'Monthly Active Users',
                        tooltip: 'Participants who sent at least one message this calendar month. The line shows the last 7 months.',
                        value: latest.mau || 0,
                        delta: deltaFor(mauSeries),
                        sparkline: sparkline(mauSeries),
                        sparklineLabel: 'Monthly active users over the last 7 months',
                        partialRangeCaption, partialRangeTitle,
                        inProgress
                    },
                    {
                        label: 'Core Users Rate',
                        tooltip: 'The share of participants this month who were active in two or more different weeks. The line shows the last 7 months.',
                        value: `${(latest.core_users_rate || 0).toFixed(1)}%`,
                        delta: deltaFor(coreSeries),
                        sparkline: sparkline(coreSeries),
                        sparklineLabel: 'Core users rate over the last 7 months',
                        partialRangeCaption, partialRangeTitle,
                        inProgress
                    },
                    {
                        label: 'Average Session Duration',
                        tooltip: 'Average length of a chat session this month, counting only sessions that have ended. The line shows the last 7 months.',
                        value: `${latestDuration.toFixed(1)} min`,
                        delta: deltaFor(durationSeries),
                        sparkline: sparkline(durationSeries),
                        sparklineLabel: 'Average session duration over the last 7 months',
                        partialRangeCaption, partialRangeTitle,
                        inProgress
                    }
                ];
            } catch (error) {
                console.error('Failed to load engagement summary:', error);
            } finally {
                this.loadingStates.summary = false;
            }
        },

        async loadFrequency() {
            this.loadingStates.frequency = true;
            try {
                const data = await this.apiRequest(this.endpoints.frequency);
                this.hasFrequencyData = data.some(row => WEEK_BUCKETS.some(bucket => row[bucket] > 0));
                if (this.hasFrequencyData) {
                    this.$nextTick(() => window.engagementChartManager.renderEngagementFrequencyChart(data));
                } else {
                    window.engagementChartManager.destroyChart('engagementFrequency');
                }
            } catch (error) {
                console.error('Failed to load engagement frequency:', error);
            } finally {
                this.loadingStates.frequency = false;
            }
        },

        async loadNewVsReturning() {
            this.loadingStates.newVsReturning = true;
            try {
                const data = await this.apiRequest(this.endpoints.newVsReturning);
                this.hasNewVsReturningData = data.some(row => row.new > 0 || row.returning > 0);
                if (this.hasNewVsReturningData) {
                    this.$nextTick(() => window.engagementChartManager.renderNewVsReturningChart(data));
                } else {
                    window.engagementChartManager.destroyChart('newVsReturning');
                }
            } catch (error) {
                console.error('Failed to load new-vs-returning data:', error);
            } finally {
                this.loadingStates.newVsReturning = false;
            }
        },

        async loadBreakdowns() {
            const panels = [
                {
                    key: 'chatbot', title: 'Top chatbots', dimensionLabel: 'Chatbot',
                    endpoint: this.endpoints.breakdownChatbot,
                    drilldown: this.links.chatbots, drilldownLabel: 'View chatbots'
                },
                {
                    key: 'channel', title: 'Engagement by channel', dimensionLabel: 'Channel',
                    endpoint: this.endpoints.breakdownChannel,
                    drilldown: this.links.sessions, drilldownLabel: 'View sessions'
                }
            ];

            this.breakdownPanels = panels.map(panel => ({...panel, rows: []}));

            await Promise.all(panels.map(async (panel, index) => {
                this.loadingStates[panel.key] = true;
                try {
                    const rows = await this.apiRequest(panel.endpoint);
                    this.breakdownPanels[index] = {
                        ...panel,
                        rows: rows.map((row, rank) => ({
                            ...row,
                            rankColor: rank < 3 ? `--viz-rank-${rank + 1}` : '--viz-rank-rest',
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
