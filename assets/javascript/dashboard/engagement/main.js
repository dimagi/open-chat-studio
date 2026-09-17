import TomSelect from "tom-select";

const TOM_SELECT_CONFIG = {
    plugins: ["remove_button", "caret_position"],
    maxItems: null,
    searchField: ['text', 'value'],
    allowEmptyOption: true,
    hideSelected: true,
    closeAfterSelect: true,
    loadThrottle: 200
};

const FILTER_KEYS = ['experiments', 'channels', 'participants', 'tags'];

function engagementDashboard() {
    return {
        filters: {experiments: [], channels: [], participants: [], tags: []},
        summaryStats: [],
        loadingStates: {summary: false, frequency: false, newVsReturning: false},
        endpoints: {summary: '', frequency: '', newVsReturning: '', sessionDuration: ''},

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
                sessionDuration: config.sessionDurationUrl
            };
        },

        setupTomSelect() {
            FILTER_KEYS.forEach(key => {
                const element = document.getElementById(`id_${key}`);
                if (!element || element.tomselect) return;
                new TomSelect(element, {...TOM_SELECT_CONFIG, onChange: () => this.handleFilterChange()});
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
            await Promise.all([this.loadSummary(), this.loadFrequency(), this.loadNewVsReturning()]);
        },

        async loadSummary() {
            this.loadingStates.summary = true;
            try {
                const [summary, avgDuration] = await Promise.all([
                    this.apiRequest(this.endpoints.summary),
                    this.apiRequest(this.endpoints.sessionDuration)
                ]);
                const latest = summary[summary.length - 1] || {};
                this.summaryStats = [
                    {
                        label: 'Monthly Active Users',
                        value: latest.mau || 0,
                        inProgress: latest.in_progress || false
                    },
                    {
                        label: 'Core Users Rate',
                        value: `${(latest.core_users_rate || 0).toFixed(1)}%`,
                        inProgress: latest.in_progress || false
                    },
                    {
                        label: 'Average Session Duration',
                        value: `${(avgDuration || 0).toFixed(1)} min`,
                        inProgress: false
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
                window.engagementChartManager.renderEngagementFrequencyChart(data);
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
                window.engagementChartManager.renderNewVsReturningChart(data);
            } catch (error) {
                console.error('Failed to load new-vs-returning data:', error);
            } finally {
                this.loadingStates.newVsReturning = false;
            }
        }
    };
}

window.engagementDashboard = engagementDashboard;
