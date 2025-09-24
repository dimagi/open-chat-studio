/**
 * Dashboard Alpine.js Component
 * Simplified reactive dashboard with Alpine.js
 */

import TomSelect from "tom-select";
import {formatDistanceToNow} from "date-fns";

// Constants
const DEFAULTS = {
    DATE_RANGE: '30',
    GRANULARITY: 'daily',
    PAGE_SIZE: 10,
    DEBOUNCE_DELAY: 500,
    NOTIFICATION_TIMEOUT: 5000,
    RELOAD_DELAY: 1000
};

const TOM_SELECT_CONFIG = {
    plugins: ["remove_button", "caret_position"],
    maxItems: null,
    searchField: ['text', 'value'],
    allowEmptyOption: true,
    hideSelected: true,
    closeAfterSelect: true,
    loadThrottle: 200
};

function dashboard() {
    return {
        // Reactive data
        filters: {
            date_range: DEFAULTS.DATE_RANGE,
            granularity: DEFAULTS.GRANULARITY,
            experiments: [],
            channels: [],
            participants: [],
            tags: [],
        },
        
        overviewStats: [],
        botPerformanceData: [],
        botPerformancePagination: {
            page: 1,
            page_size: DEFAULTS.PAGE_SIZE,
            total_count: 0,
            total_pages: 0,
            has_next: false,
            has_previous: false,
            order_by: 'messages',
            order_dir: 'desc'
        },
        userEngagementData: [],
        tagAnalyticsData: {},
        
        loadingStates: {
            overview: false,
            activeParticipants: false,
            sessionAnalytics: false,
            messageVolume: false,
            channelBreakdown: false,
            botPerformance: false,
            userEngagement: false,
            tagAnalytics: false,
            averageResponseTime: false
        },
        
        activeFilterId: null,
        saving: false,
        
        initialLoad: true,
        
        // Initialization
        init() {
            this.loadFiltersFromURL();
            this.updateFiltersFromForm();
            this.loadInitialData();
            this.setupFilterWatchers();
            this.setupTomSelect();
            
            this.initialLoad = false;
        },
        
        setupTomSelect() {
            this.initializeTomSelect('id_experiments', 'experiments', 'Select chatbots...');
            this.initializeTomSelect('id_channels', 'channels', 'Select channels...');
            this.initializeTomSelect('id_participants', 'participants', 'Select participants...');
            this.initializeTomSelect('id_tags', 'tags', 'Select tags...');
        },
        
        initializeTomSelect(elementId, filterKey, placeholder = null) {
            const selectElement = document.getElementById(elementId);
            if (!selectElement || selectElement.tomselect) return;
            
            const config = {
                ...TOM_SELECT_CONFIG,
                onChange: () => this.handleFilterChange()
            };
            
            if (placeholder) {
                config.placeholder = placeholder;
            }
            
            const tomSelect = new TomSelect(selectElement, config);
            
            // Apply URL-loaded values
            const filterValues = this.filters[filterKey];
            if (filterValues && Array.isArray(filterValues)) {
                filterValues.forEach(value => {
                    tomSelect.addItem(value, true);
                });
            }
        },
        
        setupFilterWatchers() {
            // Watch for filter changes to auto-refresh and update URL
            this.$watch('filters', () => {
                if (!this.initialLoad) {
                    this.updateURL();
                    this.debounceRefresh();
                }
            }, { deep: true });
        },
        
        // Filter management
        updateFiltersFromForm() {
            const form = document.getElementById('filterForm');
            if (!form) return;
            
            const formData = new FormData(form);
            this.filters = {};
            
            for (let [key, value] of formData.entries()) {
                if (value) {
                    if (this.filters[key]) {
                        if (!Array.isArray(this.filters[key])) {
                            this.filters[key] = [this.filters[key]];
                        }
                        this.filters[key].push(value);
                    } else {
                        this.filters[key] = value;
                    }
                }
            }
        },
        
        handleFilterChange() {
            this.updateFiltersFromForm();
        },
        
        // URL synchronization methods
        loadFiltersFromURL() {
            const urlParams = new URLSearchParams(window.location.search);
            const filtersFromURL = {};
            
            for (const [key, value] of urlParams.entries()) {
                if (key === 'experiments' || key === 'channels' || key === 'participants' || key === 'tags') {
                    // Handle multi-select fields
                    if (filtersFromURL[key]) {
                        if (!Array.isArray(filtersFromURL[key])) {
                            filtersFromURL[key] = [filtersFromURL[key]];
                        }
                        filtersFromURL[key].push(value);
                    } else {
                        filtersFromURL[key] = [value];
                    }
                } else if (key === 'date_range' || key === 'granularity') {
                    filtersFromURL[key] = value;
                } else if (key === 'start_date' || key === 'end_date') {
                    // Only load start_date and end_date if date_range is 'custom'
                    if (urlParams.get('date_range') === 'custom') {
                        filtersFromURL[key] = value;
                    }
                }
            }
            
            // Apply URL filters to form and reactive state
            if (Object.keys(filtersFromURL).length > 0) {
                this.applyFiltersToForm(filtersFromURL);
                this.filters = { ...this.filters, ...filtersFromURL };
            }
        },
        
        applyFiltersToForm(filterData) {
            const form = document.getElementById('filterForm');
            if (!form) return;
            
            for (const [key, value] of Object.entries(filterData)) {
                const element = form.querySelector(`[name="${key}"]`);
                if (element) {
                    if (element.type === 'select-multiple') {
                        Array.from(element.options).forEach(option => {
                            option.selected = Array.isArray(value) 
                                ? value.includes(option.value) 
                                : value === option.value;
                        });
                    } else {
                        element.value = Array.isArray(value) ? value[0] : value;
                    }
                }
            }
        },
        
        updateURL() {
            const url = new URL(window.location);
            const params = new URLSearchParams();
            
            // Add filters to URL params
            for (const [key, value] of Object.entries(this.filters)) {
                if (value && value !== '' && !(Array.isArray(value) && value.length === 0)) {
                    // Only include start_date and end_date if date_range is 'custom'
                    if ((key === 'start_date' || key === 'end_date') && this.filters.date_range !== 'custom') {
                        continue;
                    }
                    
                    if (Array.isArray(value)) {
                        value.forEach(v => params.append(key, v));
                    } else {
                        params.set(key, value);
                    }
                }
            }
            
            url.search = params.toString();
            window.history.replaceState({}, '', url);
        },
        
        debounceRefresh() {
            clearTimeout(this.refreshTimeout);
            this.refreshTimeout = setTimeout(() => {
                this.refreshAllCharts();
            }, DEFAULTS.DEBOUNCE_DELAY);
        },
        
        resetFilters() {
            // Reset form
            const form = document.getElementById('filterForm');
            if (form) {
                form.reset();
                
                // Set default values
                const dateRangeSelect = form.querySelector('[data-filter-type="date_range"]');
                if (dateRangeSelect) dateRangeSelect.value = DEFAULTS.DATE_RANGE;
                
                const granularitySelect = form.querySelector('[data-filter-type="granularity"]');
                if (granularitySelect) granularitySelect.value = DEFAULTS.GRANULARITY;
                
                // Clear TomSelect instances
                this.clearTomSelectInstances();
            }
            
            // Reset reactive data
            this.filters = {
                date_range: DEFAULTS.DATE_RANGE,
                granularity: DEFAULTS.GRANULARITY,
                experiments: [],
                channels: [],
                participants: [],
                tags: [],
            };
            
            this.activeFilterId = null;
            
            // Clear URL parameters
            const url = new URL(window.location);
            url.search = '';
            window.history.replaceState({}, '', url);
        },
        
        // API helpers
        async apiRequest(endpoint, params = {}) {
            if (!endpoint || typeof endpoint !== 'string') {
                throw new Error('Invalid endpoint provided');
            }
            
            const sanitizedParams = this.sanitizeParams({...this.filters, ...params});
            const urlParams = new URLSearchParams();

            for (const [key, value] of Object.entries(sanitizedParams)) {
                if (Array.isArray(value)) {
                    // Add each array item as separate parameter
                    value.forEach(item => urlParams.append(key, item));
                } else {
                    urlParams.set(key, value);
                }
            }
            
            try {
                const response = await fetch(`${endpoint}?${urlParams}`);
                
                if (!response.ok) {
                    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
                }
                
                return await response.json();
            } catch (error) {
                console.error(`API request to ${endpoint} failed:`, error);
                throw error;
            }
        },
        
        sanitizeParams(params) {
            const sanitized = {};
            
            for (const [key, value] of Object.entries(params)) {
                if (value !== null && value !== undefined && value !== '') {
                    if (Array.isArray(value)) {
                        const validValues = value.filter(v => v !== null && v !== undefined && v !== '');
                        if (validValues.length > 0) {
                            sanitized[key] = validValues;
                        }
                    } else {
                        sanitized[key] = value;
                    }
                }
            }
            
            return sanitized;
        },
        
        setLoadingState(key, loading) {
            this.loadingStates[key] = loading;
        },
        
        // Data loading methods
        loadInitialData() {
            this.refreshAllCharts();
        },
        
        async refreshAllCharts() {
            await Promise.all([
                this.loadOverviewStats(),
                this.loadSessionAnalyticsChart(),
                this.loadMessageVolumeChart(),
                this.loadChannelBreakdownChart(),
                this.loadBotPerformanceData(),
                this.loadUserEngagementData(),
                this.loadTagAnalytics(),
                this.loadAverageResponseTimeChart()
            ]);
        },
        
        async loadOverviewStats() {
            this.setLoadingState('overview', true);
            
            try {
                const data = await this.apiRequest('api/overview/');
                this.overviewStats = [
                    {
                        label: 'Active Chatbots',
                        numerator: data.active_experiments || 0,
                        denominator: data.total_experiments || 0,
                        icon: 'fas fa-robot',
                        color: 'text-blue-500'
                    },
                    {
                        label: 'Active Participants',
                        numerator: data.active_participants || 0,
                        denominator: data.total_participants || 0,
                        icon: 'fas fa-users',
                        color: 'text-green-500'
                    },
                    {
                        label: 'Completed Sessions',
                        numerator: data.completed_sessions || 0,
                        denominator: data.total_sessions || 0,
                        icon: 'fas fa-comments',
                        color: 'text-purple-500'
                    },
                    {
                        label: 'Total Messages',
                        numerator: data.total_messages || 0,
                        icon: 'fas fa-envelope',
                        color: 'text-orange-500'
                    },
                ];
            } catch (error) {
                console.error('Error loading overview stats:', error);
                this.showNotification('Failed to load overview statistics', 'error');
            } finally {
                this.setLoadingState('overview', false);
            }
        },
        
        async loadSessionAnalyticsChart() {
            this.setLoadingState('activeParticipants', true);
            this.setLoadingState('sessionAnalytics', true);
            
            try {
                const data = await this.apiRequest('api/session-analytics/');
                if (window.chartManager) {
                    window.chartManager.renderSessionAnalyticsChart(data.sessions);
                    window.chartManager.renderActiveParticipantsChart(data.participants);
                }
            } catch (error) {
                console.error('Error loading session analytics chart:', error);
                this.showChartError('sessionAnalyticsChart', 'Failed to load session analytics data');
                this.showChartError('activeParticipantsChart', 'Failed to load session analytics data');
            } finally {
                this.setLoadingState('sessionAnalytics', false);
                this.setLoadingState('activeParticipants', false);
            }
        },
        
        async loadMessageVolumeChart() {
            this.setLoadingState('messageVolume', true);
            
            try {
                const data = await this.apiRequest('api/message-volume/');
                if (window.chartManager) {
                    window.chartManager.renderMessageVolumeChart(data);
                }
            } catch (error) {
                console.error('Error loading message volume chart:', error);
                this.showChartError('messageVolumeChart', 'Failed to load message volume data');
            } finally {
                this.setLoadingState('messageVolume', false);
            }
        },
        
        async loadChannelBreakdownChart() {
            this.setLoadingState('channelBreakdown', true);
            
            try {
                const data = await this.apiRequest('api/channel-breakdown/');
                if (window.chartManager) {
                    window.chartManager.renderChannelBreakdownChart(data);
                }
            } catch (error) {
                console.error('Error loading channel breakdown chart:', error);
                this.showChartError('channelBreakdownChart', 'Failed to load channel breakdown data');
            } finally {
                this.setLoadingState('channelBreakdown', false);
            }
        },
        
        async loadBotPerformanceData(page = null, order_by = null, order_dir = null) {
            this.setLoadingState('botPerformance', true);
            
            try {
                // Use provided parameters or current pagination state
                const currentPage = page || this.botPerformancePagination.page;
                const currentOrderBy = order_by || this.botPerformancePagination.order_by;
                const currentOrderDir = order_dir || this.botPerformancePagination.order_dir;
                
                // Pass pagination/sorting params to apiRequest which will merge with filters
                const params = {
                    page: currentPage,
                    page_size: this.botPerformancePagination.page_size,
                    order_by: currentOrderBy,
                    order_dir: currentOrderDir
                };
                
                const data = await this.apiRequest('api/bot-performance/', params);
                
                this.botPerformanceData = data.results || [];
                this.botPerformancePagination = {
                    page: data.page || 1,
                    page_size: data.page_size || 10,
                    total_count: data.total_count || 0,
                    total_pages: data.total_pages || 0,
                    has_next: data.has_next || false,
                    has_previous: data.has_previous || false,
                    order_by: data.order_by || 'messages',
                    order_dir: data.order_dir || 'desc'
                };
            } catch (error) {
                console.error('Error loading bot performance data:', error);
                this.showNotification('Failed to load bot performance data', 'error');
            } finally {
                this.setLoadingState('botPerformance', false);
            }
        },
        
        async loadUserEngagementData() {
            this.setLoadingState('userEngagement', true);
            
            try {
                const data = await this.apiRequest('api/user-engagement/');
                this.userEngagementData = data.most_active_participants || [];
                
                if (window.chartManager && data.session_length_distribution) {
                    window.chartManager.renderSessionLengthChart(data.session_length_distribution);
                }
            } catch (error) {
                console.error('Error loading user engagement data:', error);
                this.showNotification('Failed to load user engagement data', 'error');
            } finally {
                this.setLoadingState('userEngagement', false);
            }
        },
        
        async loadTagAnalytics() {
            this.setLoadingState('tagAnalytics', true);
            
            try {
                const data = await this.apiRequest('api/tag-analytics/');
                this.tagAnalyticsData = data;
            } catch (error) {
                console.error('Error loading tag analytics:', error);
                this.tagAnalyticsData = { total_tagged_messages: 0 };
            } finally {
                this.setLoadingState('tagAnalytics', false);
            }
        },
        
        // Saved filters
        async loadSavedFilter(filterId) {
            try {
                const response = await fetch(`filters/load/${filterId}/`);
                const data = await response.json();
                
                if (data.success) {
                    this.applyFilterData(data.filter_data);
                    this.activeFilterId = filterId;
                    this.showNotification('Filter loaded successfully', 'success');
                } else {
                    this.showNotification('Failed to load filter', 'error');
                }
            } catch (error) {
                console.error('Error loading saved filter:', error);
                this.showNotification('Error loading filter', 'error');
            }
        },

        async loadAverageResponseTimeChart() {
            this.setLoadingState('averageResponseTime', true);
            try {
                const data = await this.apiRequest('api/average-response-time/');
                if (window.chartManager) {
                    window.chartManager.renderAverageResponseTimeChart(data);
                }
            } catch (error) {
                console.error('Error loading avg response time:', error);
                this.showChartError('averageResponseTimeChart', 'Failed to load average response time');
            } finally {
                this.setLoadingState('averageResponseTime', false);
            }
        },
        
        applyFilterData(filterData) {
            const form = document.getElementById('filterForm');
            if (!form) return;
            
            // Clear and apply values
            form.reset();
            
            for (const [key, value] of Object.entries(filterData)) {
                const element = form.querySelector(`[name="${key}"]`);
                if (element) {
                    // Handle TomSelect instances
                    if (element.tomselect) {
                        element.tomselect.clear();
                        if (Array.isArray(value)) {
                            value.forEach(v => element.tomselect.addItem(v, true));
                        } else if (value) {
                            element.tomselect.addItem(value, true);
                        }
                    } else if (element.type === 'select-multiple') {
                        Array.from(element.options).forEach(option => {
                            option.selected = Array.isArray(value) 
                                ? value.includes(option.value) 
                                : value === option.value;
                        });
                    } else {
                        element.value = value;
                    }
                }
            }
            
            // Update reactive state
            this.filters = {...filterData};
        },
        
        async handleSaveFilter() {
            const form = document.getElementById('saveFilterForm');
            if (!form) {
                console.error('saveFilterForm not found');
                return;
            }
            
            this.saving = true;

            try {
                const formData = new FormData(form);
                formData.set('filter_data', JSON.stringify(this.filters));

                const response = await fetch('filters/save/', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': this.getCSRFToken()
                    }
                });

                const data = await response.json();

                if (data.success) {
                    this.showNotification('Filter saved successfully', 'success');

                    // Refresh page to show new saved filter
                    setTimeout(() => window.location.reload(), DEFAULTS.RELOAD_DELAY);
                } else {
                    this.showNotification('Failed to save filter', 'error');
                }
            } catch (error) {
                console.error('Save filter error:', error);
                this.showNotification('Failed to save filter', 'error');
            } finally {
                this.saving = false;
            }
        },
        
        async deleteSavedFilter(filterId, filterName) {
            if (!confirm(`Are you sure you want to delete the filter "${filterName}"?`)) {
                return;
            }
            
            try {
                const response = await fetch(`filters/delete/${filterId}/`, {
                    method: 'DELETE',
                    headers: {
                        'X-CSRFToken': this.getCSRFToken(),
                        'Content-Type': 'application/json'
                    }
                });
                
                const data = await response.json();
                
                if (data.success) {
                    this.showNotification('Filter deleted successfully', 'success');
                    
                    // Clear active filter if it was the one deleted
                    if (this.activeFilterId === filterId) {
                        this.activeFilterId = null;
                    }
                    
                    // Refresh page to update saved filters list
                    setTimeout(() => window.location.reload(), DEFAULTS.RELOAD_DELAY);
                } else {
                    this.showNotification('Failed to delete filter', 'error');
                }
            } catch (error) {
                console.error('Delete filter error:', error);
                this.showNotification('Failed to delete filter', 'error');
            }
        },
        
        // Error handling
        showChartError(chartId, message) {
            const canvas = document.getElementById(chartId);
            if (canvas) {
                const container = canvas.parentElement;
                container.innerHTML = `
                    <div class="chart-error">
                        <div class="chart-error-icon">⚠️</div>
                        <div class="chart-error-message">${message}</div>
                        <button class="chart-retry-btn" @click="refreshAllCharts()">
                            Try Again
                        </button>
                    </div>
                `;
            }
        },
        
        showNotification(message, type = 'info') {
            // Create notification element
            const notification = document.createElement('div');
            notification.className = `alert alert-${type} fixed top-4 right-4 z-50 shadow-lg max-w-sm fade-in`;
            notification.innerHTML = `
                <div class="flex items-center gap-2">
                    <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-triangle' : 'info-circle'}"></i>
                    <span>${message}</span>
                </div>
            `;
            
            document.body.appendChild(notification);
            
            // Auto-remove after timeout
            setTimeout(() => {
                if (notification.parentElement) {
                    notification.remove();
                }
            }, DEFAULTS.NOTIFICATION_TIMEOUT);
        },
        
        // Utility methods
        formatNumber(num) {
            if (num >= 1000000) {
                return (num / 1000000).toFixed(1) + 'M';
            }
            if (num >= 1000) {
                return (num / 1000).toFixed(1) + 'K';
            }
            return num.toString();
        },
        
        formatDuration(minutes) {
            if (minutes < 60) {
                return `${Math.round(minutes)}m`;
            }
            const hours = Math.floor(minutes / 60);
            const mins = Math.round(minutes % 60);
            return `${hours}h ${mins}m`;
        },

        formatDate(date) {
            return formatDistanceToNow(new Date(date));
        },
        
        getCSRFToken() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        },
        
        // Bot Performance Pagination and Sorting Methods
        changeBotPerformancePage(page) {
            this.loadBotPerformanceData(page);
        },
        
        sortBotPerformance(column) {
            let order_dir = 'desc';
            if (this.botPerformancePagination.order_by === column && this.botPerformancePagination.order_dir === 'desc') {
                order_dir = 'asc';
            }
            this.loadBotPerformanceData(1, column, order_dir);
        },
        
        getBotPerformancePageNumbers() {
            const pages = [];
            const current = this.botPerformancePagination.page;
            const total = this.botPerformancePagination.total_pages;
            
            // Always show first page
            if (total > 0) pages.push(1);
            
            // Add pages around current page
            for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
                if (!pages.includes(i)) pages.push(i);
            }
            
            // Always show last page
            if (total > 1 && !pages.includes(total)) pages.push(total);
            
            return pages;
        },
        
        getSortIcon(column) {
            if (this.botPerformancePagination.order_by !== column) {
                return 'fas fa-sort';
            }
            return this.botPerformancePagination.order_dir === 'asc' ? 'fas fa-sort-up' : 'fas fa-sort-down';
        },
        
        clearTomSelectInstances() {
            ['id_experiments', 'id_channels', 'id_participants', 'id_tags'].forEach(id => {
                const element = document.getElementById(id);
                if (element && element.tomselect) {
                    element.tomselect.clear();
                }
            });
        },

        getDynamicFiltersUrl(allSessionsUrl, tagName) {
            const urlParams = this.buildBaseTagDynamicFilter(tagName);
            this.addMappedDynamicFilters(urlParams);
            return `${allSessionsUrl}?${urlParams.toString()}`;
        },

        buildBaseTagDynamicFilter(tagName) {
            const urlParams = new URLSearchParams();
            urlParams.append("filter_0_column", "tags");
            urlParams.append("filter_0_value", JSON.stringify([tagName]));
            urlParams.append("filter_0_operator", "any of");
            return urlParams;
        },

        addMappedDynamicFilters(urlParams) {
            const dynamicFilterParamMapping = {
                "experiments": "experiment",
                "participants": "participant",
                "start_date": "message_date",
                "end_date": "message_date",
                "date_range": "message_date",
            };
            let params = this.sanitizeParams(this.filters);
            Object.entries(params).forEach(([key, value], index) => {
                if (key === "granularity" || key === "tags" || value === "custom" ||key === "participants") {
                    // dynamic filters do not support granularity, and the tags filter is already added
                    return;
                }
                
                let parsedValue = "";
                // Map the filter keys to the expected query params in the all sessions view
                let keyMapped = dynamicFilterParamMapping[key] || key;
                let operator = "any of";
                if (key === "start_date") {
                    operator = "after";
                    // To account for filter mismatches, we add one day to the start date
                    parsedValue = this.shiftDay(value, -1); // keep date as string
                } else if (key === "end_date") {
                    operator = "before";
                    // To account for filter mismatches, we add one day to the end date
                    parsedValue = this.shiftDay(value, 1); // keep date as string
                } else if (key === "date_range") {
                    operator = "range";
                    parsedValue = value + "d";
                } else {
                    // Non-date fields expects array values
                    parsedValue = JSON.stringify((Array.isArray(value) ? value : [value]))
                }
                urlParams.append(`filter_${index + 1}_column`, keyMapped);
                urlParams.append(`filter_${index + 1}_value`, parsedValue);
                urlParams.append(`filter_${index + 1}_operator`, operator);
            });
        },

        shiftDay(dateString, amount) {
            let date = new Date(dateString + 'T00:00:00.000Z');
            date.setUTCDate(date.getUTCDate() + amount);
            return date.toISOString().split('T')[0];
        },

        // Cleanup
        destroy() {
            if (this.refreshTimeout) {
                clearTimeout(this.refreshTimeout);
            }
        }
    };
}

// Make dashboard function globally available
window.dashboard = dashboard;
