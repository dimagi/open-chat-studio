/**
 * Dashboard Main JavaScript
 * Handles dashboard initialization, data loading, and UI interactions
 */

class Dashboard {
    constructor() {
        this.charts = {};
        this.currentFilters = {};
        this.loadingStates = new Set();
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.loadInitialData();
        this.setupAutoRefresh();
    }
    
    setupEventListeners() {
        // Filter form changes
        const filterForm = document.getElementById('filterForm');
        if (filterForm) {
            filterForm.addEventListener('change', () => this.handleFilterChange());
        }
        
        // Date range selector
        const dateRangeSelect = document.querySelector('[data-filter-type="date_range"]');
        if (dateRangeSelect) {
            dateRangeSelect.addEventListener('change', () => this.handleDateRangeChange());
        }
        
        // Reset filters
        const resetBtn = document.getElementById('resetFilters');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => this.resetFilters());
        }
        
        // Saved filter buttons
        document.querySelectorAll('.saved-filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.loadSavedFilter(e.target.dataset.filterId));
        });
        
        // Export form
        const exportForm = document.getElementById('exportForm');
        if (exportForm) {
            exportForm.addEventListener('submit', (e) => this.handleExport(e));
        }
        
        // Save filter form
        const saveFilterForm = document.getElementById('saveFilterForm');
        if (saveFilterForm) {
            saveFilterForm.addEventListener('submit', (e) => this.handleSaveFilter(e));
        }
        
        // Modal trigger buttons
        const exportBtn = document.getElementById('exportBtn');
        if (exportBtn) {
            exportBtn.addEventListener('click', () => {
                const modal = document.getElementById('exportModal');
                if (modal) {
                    modal.classList.add('modal-open');
                }
            });
        }
        
        const saveFiltersBtn = document.getElementById('saveFiltersBtn');
        if (saveFiltersBtn) {
            saveFiltersBtn.addEventListener('click', () => {
                const modal = document.getElementById('filtersModal');
                if (modal) {
                    modal.classList.add('modal-open');
                }
            });
        }
    }
    
    handleFilterChange() {
        // Debounce filter changes
        clearTimeout(this.filterTimeout);
        this.filterTimeout = setTimeout(() => {
            this.updateCurrentFilters();
            this.refreshAllCharts();
        }, 500);
    }
    
    handleDateRangeChange() {
        const dateRangeSelect = document.querySelector('[data-filter-type="date_range"]');
        const customDateRange = document.getElementById('customDateRange');
        const customDateRangeEnd = document.getElementById('customDateRangeEnd');
        
        if (dateRangeSelect?.value === 'custom') {
            customDateRange.style.display = 'block';
            customDateRangeEnd.style.display = 'block';
        } else {
            customDateRange.style.display = 'none';
            customDateRangeEnd.style.display = 'none';
        }
        
        this.handleFilterChange();
    }
    
    updateCurrentFilters() {
        const formData = new FormData(document.getElementById('filterForm'));
        this.currentFilters = {};
        
        for (let [key, value] of formData.entries()) {
            if (value) {
                if (this.currentFilters[key]) {
                    // Handle multiple values (e.g., multiple experiments)
                    if (!Array.isArray(this.currentFilters[key])) {
                        this.currentFilters[key] = [this.currentFilters[key]];
                    }
                    this.currentFilters[key].push(value);
                } else {
                    this.currentFilters[key] = value;
                }
            }
        }
    }
    
    resetFilters() {
        const filterForm = document.getElementById('filterForm');
        if (filterForm) {
            filterForm.reset();
            // Set default values
            const dateRangeSelect = document.querySelector('[data-filter-type="date_range"]');
            if (dateRangeSelect) {
                dateRangeSelect.value = '30';
            }
            
            const granularitySelect = document.querySelector('[data-filter-type="granularity"]');
            if (granularitySelect) {
                granularitySelect.value = 'daily';
            }
        }
        
        this.handleDateRangeChange();
        this.handleFilterChange();
    }
    
    async loadSavedFilter(filterId) {
        try {
            const response = await fetch(`filters/load/${filterId}/`);
            const data = await response.json();
            
            if (data.success) {
                this.applyFilterData(data.filter_data);
                this.showNotification('Filter loaded successfully', 'success');
            } else {
                this.showNotification('Failed to load filter', 'error');
            }
        } catch (error) {
            console.error('Error loading saved filter:', error);
            this.showNotification('Error loading filter', 'error');
        }
    }
    
    applyFilterData(filterData) {
        const filterForm = document.getElementById('filterForm');
        if (!filterForm) return;
        
        // Clear current form
        filterForm.reset();
        
        // Apply saved filter values
        for (const [key, value] of Object.entries(filterData)) {
            const element = filterForm.querySelector(`[name="${key}"]`);
            if (element) {
                if (element.type === 'select-multiple') {
                    Array.from(element.options).forEach(option => {
                        option.selected = Array.isArray(value) ? value.includes(option.value) : value === option.value;
                    });
                } else {
                    element.value = value;
                }
            }
        }
        
        this.handleDateRangeChange();
        this.handleFilterChange();
    }
    
    loadInitialData() {
        this.updateCurrentFilters();
        this.loadOverviewStats();
        this.refreshAllCharts();
    }
    
    refreshAllCharts() {
        this.loadOverviewStats();
        this.loadActiveParticipantsChart();
        this.loadSessionAnalyticsChart();
        this.loadMessageVolumeChart();
        this.loadBotPerformanceTable();
        this.loadUserEngagementData();
        this.loadChannelBreakdownChart();
        this.loadTagAnalytics();
    }
    
    setLoadingState(chartId, isLoading) {
        const loadingElement = document.getElementById(`${chartId}Loading`);
        if (loadingElement) {
            loadingElement.style.display = isLoading ? 'block' : 'none';
        }
        
        if (isLoading) {
            this.loadingStates.add(chartId);
        } else {
            this.loadingStates.delete(chartId);
        }
    }
    
    async loadOverviewStats() {
        this.setLoadingState('overview', true);
        
        try {
            const params = new URLSearchParams(this.currentFilters);
            const response = await fetch(`api/overview/?${params}`);
            const data = await response.json();
            
            this.renderOverviewStats(data);
        } catch (error) {
            console.error('Error loading overview stats:', error);
            this.showChartError('overviewStats', 'Failed to load overview statistics');
        } finally {
            this.setLoadingState('overview', false);
        }
    }
    
    renderOverviewStats(data) {
        const container = document.getElementById('overviewStats');
        if (!container) return;
        
        const stats = [
            {
                label: 'Total Experiments',
                value: data.total_experiments || 0,
                icon: 'fas fa-robot',
                color: 'blue'
            },
            {
                label: 'Active Participants',
                value: data.active_participants || 0,
                icon: 'fas fa-users',
                color: 'green'
            },
            {
                label: 'Total Sessions',
                value: data.total_sessions || 0,
                icon: 'fas fa-comments',
                color: 'purple'
            },
            {
                label: 'Total Messages',
                value: data.total_messages || 0,
                icon: 'fas fa-envelope',
                color: 'orange'
            }
        ];
        
        container.innerHTML = stats.map(stat => `
            <div class="stat-card fade-in">
                <div class="flex items-center justify-between">
                    <div>
                        <div class="stat-value">${this.formatNumber(stat.value)}</div>
                        <div class="stat-label">${stat.label}</div>
                    </div>
                    <div class="text-2xl text-${stat.color}-500">
                        <i class="${stat.icon}"></i>
                    </div>
                </div>
            </div>
        `).join('');
    }
    
    async apiRequest(endpoint, params = {}) {
        const urlParams = new URLSearchParams({...this.currentFilters, ...params});
        const response = await fetch(`${endpoint}?${urlParams}`);
        
        if (!response.ok) {
            throw new Error(`API request failed: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    async loadActiveParticipantsChart() {
        this.setLoadingState('activeParticipants', true);
        
        try {
            const data = await this.apiRequest('api/active-participants/');
            window.chartManager.renderActiveParticipantsChart(data);
        } catch (error) {
            console.error('Error loading active participants chart:', error);
            this.showChartError('activeParticipantsChart', 'Failed to load active participants data');
        } finally {
            this.setLoadingState('activeParticipants', false);
        }
    }
    
    async loadSessionAnalyticsChart() {
        this.setLoadingState('sessionAnalytics', true);
        
        try {
            const data = await this.apiRequest('api/session-analytics/');
            window.chartManager.renderSessionAnalyticsChart(data);
        } catch (error) {
            console.error('Error loading session analytics chart:', error);
            this.showChartError('sessionAnalyticsChart', 'Failed to load session analytics data');
        } finally {
            this.setLoadingState('sessionAnalytics', false);
        }
    }
    
    async loadMessageVolumeChart() {
        this.setLoadingState('messageVolume', true);
        
        try {
            const data = await this.apiRequest('api/message-volume/');
            window.chartManager.renderMessageVolumeChart(data);
        } catch (error) {
            console.error('Error loading message volume chart:', error);
            this.showChartError('messageVolumeChart', 'Failed to load message volume data');
        } finally {
            this.setLoadingState('messageVolume', false);
        }
    }
    
    async loadChannelBreakdownChart() {
        this.setLoadingState('channelBreakdown', true);
        
        try {
            const data = await this.apiRequest('api/channel-breakdown/');
            window.chartManager.renderChannelBreakdownChart(data);
        } catch (error) {
            console.error('Error loading channel breakdown chart:', error);
            this.showChartError('channelBreakdownChart', 'Failed to load channel breakdown data');
        } finally {
            this.setLoadingState('channelBreakdown', false);
        }
    }
    
    async loadBotPerformanceTable() {
        this.setLoadingState('botPerformance', true);
        
        try {
            const data = await this.apiRequest('api/bot-performance/');
            this.renderBotPerformanceTable(data);
        } catch (error) {
            console.error('Error loading bot performance table:', error);
            this.showTableError('botPerformanceTable', 'Failed to load bot performance data');
        } finally {
            this.setLoadingState('botPerformance', false);
        }
    }
    
    renderBotPerformanceTable(data) {
        const tbody = document.querySelector('#botPerformanceTable tbody');
        if (!tbody) return;
        
        tbody.innerHTML = data.map(item => `
            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700">
                <td class="font-medium">${this.escapeHtml(item.experiment_name)}</td>
                <td>${this.formatNumber(item.participants)}</td>
                <td>${this.formatNumber(item.sessions)}</td>
                <td>${this.formatNumber(item.messages)}</td>
                <td>${item.avg_session_duration ? this.formatDuration(item.avg_session_duration) : '-'}</td>
                <td>
                    <div class="flex items-center gap-2">
                        <div class="progress-bar flex-1">
                            <div class="progress-bar-fill success" style="width: ${(item.completion_rate * 100).toFixed(1)}%"></div>
                        </div>
                        <span class="text-xs">${(item.completion_rate * 100).toFixed(1)}%</span>
                    </div>
                </td>
            </tr>
        `).join('');
    }
    
    async loadUserEngagementData() {
        this.setLoadingState('userEngagement', true);
        
        try {
            const data = await this.apiRequest('api/user-engagement/');
            this.renderUserEngagementData(data);
            window.chartManager.renderSessionLengthChart(data.session_length_distribution || []);
        } catch (error) {
            console.error('Error loading user engagement data:', error);
            this.showTableError('mostActiveTable', 'Failed to load user engagement data');
        } finally {
            this.setLoadingState('userEngagement', false);
        }
    }
    
    renderUserEngagementData(data) {
        const tbody = document.querySelector('#mostActiveTable tbody');
        if (!tbody) return;
        
        const participants = data.most_active_participants || [];
        tbody.innerHTML = participants.map(item => `
            <tr>
                <td class="font-medium">${this.escapeHtml(item.participant_name)}</td>
                <td>${this.formatNumber(item.total_messages)}</td>
                <td>${this.formatNumber(item.total_sessions)}</td>
            </tr>
        `).join('');
    }
    
    async loadTagAnalytics() {
        this.setLoadingState('tagAnalytics', true);
        
        try {
            const data = await this.apiRequest('api/tag-analytics/');
            
            if (data.total_tagged_messages > 0) {
                this.renderTagAnalytics(data);
                document.getElementById('tagAnalyticsSection').style.display = 'block';
            } else {
                document.getElementById('tagAnalyticsSection').style.display = 'none';
            }
        } catch (error) {
            console.error('Error loading tag analytics:', error);
            document.getElementById('tagAnalyticsSection').style.display = 'none';
        } finally {
            this.setLoadingState('tagAnalytics', false);
        }
    }
    
    renderTagAnalytics(data) {
        const container = document.getElementById('tagAnalyticsContent');
        if (!container) return;
        
        const categories = data.tag_categories || {};
        
        container.innerHTML = Object.entries(categories).map(([category, tags]) => `
            <div class="tag-category">
                <h4 class="tag-category-title">${this.escapeHtml(category)}</h4>
                <div class="tag-list">
                    ${Object.entries(tags).map(([tagName, count]) => `
                        <div class="tag-item">
                            <span class="tag-name">${this.escapeHtml(tagName)}</span>
                            <span class="tag-count">${count}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');
    }
    
    setupAutoRefresh() {
        // Auto-refresh every 5 minutes
        setInterval(() => {
            if (document.visibilityState === 'visible' && this.loadingStates.size === 0) {
                this.refreshAllCharts();
            }
        }, 5 * 60 * 1000);
    }
    
    async handleExport(event) {
        event.preventDefault();
        
        const form = event.target;
        const formData = new FormData(form);
        
        // Add current filter data if requested
        if (formData.get('include_filters')) {
            formData.set('filter_data', JSON.stringify(this.currentFilters));
        }
        
        try {
            const response = await fetch('export/', {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': this.getCSRFToken()
                }
            });
            
            if (response.ok) {
                const contentType = response.headers.get('content-type');
                if (contentType && contentType.includes('application/json')) {
                    const data = await response.json();
                    if (!data.success) {
                        this.showNotification(data.error || 'Export failed', 'error');
                        return;
                    }
                } else {
                    // Handle file download
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = this.getFilenameFromResponse(response) || 'dashboard_export';
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                    
                    this.showNotification('Export completed successfully', 'success');
                    document.getElementById('exportModal').classList.remove('modal-open');
                }
            } else {
                this.showNotification('Export failed', 'error');
            }
        } catch (error) {
            console.error('Export error:', error);
            this.showNotification('Export failed', 'error');
        }
    }
    
    async handleSaveFilter(event) {
        event.preventDefault();
        
        const form = event.target;
        const formData = new FormData(form);
        formData.set('filter_data', JSON.stringify(this.currentFilters));
        
        try {
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
                document.getElementById('filtersModal').classList.remove('modal-open');
                // Refresh saved filters list
                setTimeout(() => window.location.reload(), 1000);
            } else {
                this.showNotification('Failed to save filter', 'error');
            }
        } catch (error) {
            console.error('Save filter error:', error);
            this.showNotification('Failed to save filter', 'error');
        }
    }
    
    showChartError(chartId, message) {
        const canvas = document.getElementById(chartId);
        if (canvas) {
            const container = canvas.parentElement;
            container.innerHTML = `
                <div class="chart-error">
                    <div class="chart-error-icon">⚠️</div>
                    <div class="chart-error-message">${message}</div>
                    <button class="chart-retry-btn" onclick="dashboard.refreshAllCharts()">
                        Try Again
                    </button>
                </div>
            `;
        }
    }
    
    showTableError(tableId, message) {
        const table = document.getElementById(tableId);
        if (table) {
            const tbody = table.querySelector('tbody');
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="100%" class="text-center py-8 text-gray-500">
                            <div class="chart-error">
                                <div class="chart-error-icon">⚠️</div>
                                <div class="chart-error-message">${message}</div>
                                <button class="chart-retry-btn" onclick="dashboard.refreshAllCharts()">
                                    Try Again
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }
        }
    }
    
    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `alert alert-${type} fixed top-4 right-4 z-50 shadow-lg max-w-sm`;
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
    }
    
    formatNumber(num) {
        if (num >= 1000000) {
            return (num / 1000000).toFixed(1) + 'M';
        }
        if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        }
        return num.toString();
    }
    
    formatDuration(minutes) {
        if (minutes < 60) {
            return `${Math.round(minutes)}m`;
        }
        const hours = Math.floor(minutes / 60);
        const mins = Math.round(minutes % 60);
        return `${hours}h ${mins}m`;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    getCSRFToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }
    
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new Dashboard();
});
