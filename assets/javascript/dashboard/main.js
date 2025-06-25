/**
 * Dashboard Alpine.js Component
 * Simplified reactive dashboard with Alpine.js
 */

import TomSelect from "tom-select";

function dashboard() {
    return {
        // Reactive data
        filters: {
            date_range: '30',
            granularity: 'daily',
            experiments: [],
            channels: []
        },
        
        overviewStats: [],
        botPerformanceData: [],
        botPerformancePagination: {
            page: 1,
            page_size: 10,
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
            tagAnalytics: false
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
            // Initialize TomSelect for experiments field
            const experimentsSelect = document.getElementById('id_experiments');
            if (experimentsSelect && !experimentsSelect.tomselect) {
                const tomSelect = new TomSelect(experimentsSelect, {
                    plugins: ["remove_button", "caret_position"],
                    maxItems: null,
                    searchField: ['text', 'value'],
                    allowEmptyOption: true,
                    hideSelected: true,
                    closeAfterSelect: true,
                    loadThrottle: 200,
                    onChange: () => {
                        this.handleFilterChange();
                    }
                });
                
                // Apply URL-loaded values to TomSelect
                if (this.filters.experiments && Array.isArray(this.filters.experiments)) {
                    this.filters.experiments.forEach(value => {
                        tomSelect.addItem(value, true);
                    });
                }
            }
            
            // Initialize TomSelect for channels field
            const channelsSelect = document.getElementById('id_channels');
            if (channelsSelect && !channelsSelect.tomselect) {
                const tomSelect = new TomSelect(channelsSelect, {
                    plugins: ["remove_button", "caret_position"],
                    maxItems: null,
                    searchField: ['text', 'value'],
                    allowEmptyOption: true,
                    hideSelected: true,
                    closeAfterSelect: true,
                    placeholder: 'Select channels...',
                    onChange: () => {
                        this.handleFilterChange();
                    }
                });
                
                // Apply URL-loaded values to TomSelect
                if (this.filters.channels && Array.isArray(this.filters.channels)) {
                    this.filters.channels.forEach(value => {
                        tomSelect.addItem(value, true);
                    });
                }
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
                if (key === 'experiments' || key === 'channels') {
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
            }, 500);
        },
        
        resetFilters() {
            // Reset form
            const form = document.getElementById('filterForm');
            if (form) {
                form.reset();
                
                // Set default values
                const dateRangeSelect = form.querySelector('[data-filter-type="date_range"]');
                if (dateRangeSelect) dateRangeSelect.value = '30';
                
                const granularitySelect = form.querySelector('[data-filter-type="granularity"]');
                if (granularitySelect) granularitySelect.value = 'daily';
                
                // Clear TomSelect instances
                const experimentsSelect = document.getElementById('id_experiments');
                if (experimentsSelect && experimentsSelect.tomselect) {
                    experimentsSelect.tomselect.clear();
                }
                
                const channelsSelect = document.getElementById('id_channels');
                if (channelsSelect && channelsSelect.tomselect) {
                    channelsSelect.tomselect.clear();
                }
            }
            
            // Reset reactive data
            this.filters = {
                date_range: '30',
                granularity: 'daily',
                experiments: [],
                channels: []
            };
            
            this.activeFilterId = null;
            
            // Clear URL parameters
            const url = new URL(window.location);
            url.search = '';
            window.history.replaceState({}, '', url);
        },
        
        // API helpers
        async apiRequest(endpoint, params = {}) {
            const urlParams = new URLSearchParams({...this.filters, ...params});
            const response = await fetch(`${endpoint}?${urlParams}`);
            
            if (!response.ok) {
                throw new Error(`API request failed: ${response.statusText}`);
            }
            
            return response.json();
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
                this.loadActiveParticipantsChart(),
                this.loadSessionAnalyticsChart(),
                this.loadMessageVolumeChart(),
                this.loadChannelBreakdownChart(),
                this.loadBotPerformanceData(),
                this.loadUserEngagementData(),
                this.loadTagAnalytics()
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
        
        async loadActiveParticipantsChart() {
            this.setLoadingState('activeParticipants', true);
            
            try {
                const data = await this.apiRequest('api/active-participants/');
                if (window.chartManager) {
                    window.chartManager.renderActiveParticipantsChart(data);
                }
            } catch (error) {
                console.error('Error loading active participants chart:', error);
                this.showChartError('activeParticipantsChart', 'Failed to load active participants data');
            } finally {
                this.setLoadingState('activeParticipants', false);
            }
        },
        
        async loadSessionAnalyticsChart() {
            this.setLoadingState('sessionAnalytics', true);
            
            try {
                const data = await this.apiRequest('api/session-analytics/');
                if (window.chartManager) {
                    window.chartManager.renderSessionAnalyticsChart(data);
                }
            } catch (error) {
                console.error('Error loading session analytics chart:', error);
                this.showChartError('sessionAnalyticsChart', 'Failed to load session analytics data');
            } finally {
                this.setLoadingState('sessionAnalytics', false);
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
                    setTimeout(() => window.location.reload(), 1000);
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
                    setTimeout(() => window.location.reload(), 1000);
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
            
            // Auto-remove after 5 seconds
            setTimeout(() => {
                if (notification.parentElement) {
                    notification.remove();
                }
            }, 5000);
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
