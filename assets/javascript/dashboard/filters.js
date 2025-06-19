/**
 * Dashboard Filters JavaScript
 * Handles filter UI interactions and state management
 */

class FilterManager {
    constructor() {
        this.filterState = {};
        this.debounceTimeout = null;
        this.debounceDelay = 300;
        
        this.init();
    }
    
    init() {
        this.setupFilterEventListeners();
        this.setupModalEventListeners();
        this.loadInitialFilterState();
    }
    
    setupFilterEventListeners() {
        // Date range changes
        const dateRangeSelect = document.querySelector('[data-filter-type="date_range"]');
        if (dateRangeSelect) {
            dateRangeSelect.addEventListener('change', () => {
                this.handleDateRangeChange();
                this.triggerFilterUpdate();
            });
        }
        
        // Custom date inputs
        const startDateInput = document.querySelector('[data-filter-type="start_date"]');
        const endDateInput = document.querySelector('[data-filter-type="end_date"]');
        
        if (startDateInput) {
            startDateInput.addEventListener('change', () => this.triggerFilterUpdate());
        }
        
        if (endDateInput) {
            endDateInput.addEventListener('change', () => this.triggerFilterUpdate());
        }
        
        // Granularity changes
        const granularitySelect = document.querySelector('[data-filter-type="granularity"]');
        if (granularitySelect) {
            granularitySelect.addEventListener('change', () => this.triggerFilterUpdate());
        }
        
        // Multi-select filters
        const experimentSelect = document.querySelector('[data-filter-type="experiments"]');
        const channelSelect = document.querySelector('[data-filter-type="channels"]');
        
        if (experimentSelect) {
            experimentSelect.addEventListener('change', () => this.triggerFilterUpdate());
        }
        
        if (channelSelect) {
            channelSelect.addEventListener('change', () => this.triggerFilterUpdate());
        }
        
        // Saved filter buttons
        document.querySelectorAll('.saved-filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.loadSavedFilter(btn.dataset.filterId);
                this.highlightActiveFilter(btn);
            });
        });
        
        // Reset filters button
        const resetBtn = document.getElementById('resetFilters');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                this.resetFilters();
                this.clearActiveFilterHighlight();
            });
        }
    }
    
    setupModalEventListeners() {
        // Save filter modal
        const saveFilterModal = document.getElementById('filtersModal');
        const saveFilterForm = document.getElementById('saveFilterForm');
        
        if (saveFilterForm) {
            saveFilterForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.handleSaveFilter();
            });
        }
        
        // Export modal
        const exportForm = document.getElementById('exportForm');
        if (exportForm) {
            exportForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.handleExport();
            });
        }
        
        // Modal backdrop clicks
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closeModal(modal);
                }
            });
        });
    }
    
    handleDateRangeChange() {
        const dateRangeSelect = document.querySelector('[data-filter-type="date_range"]');
        const customDateRange = document.getElementById('customDateRange');
        const customDateRangeEnd = document.getElementById('customDateRangeEnd');
        
        if (!dateRangeSelect || !customDateRange || !customDateRangeEnd) return;
        
        if (dateRangeSelect.value === 'custom') {
            customDateRange.style.display = 'block';
            customDateRangeEnd.style.display = 'block';
            
            // Focus on start date input
            const startDateInput = customDateRange.querySelector('input');
            if (startDateInput) {
                startDateInput.focus();
            }
        } else {
            customDateRange.style.display = 'none';
            customDateRangeEnd.style.display = 'none';
            
            // Set automatic date range
            this.setAutomaticDateRange(dateRangeSelect.value);
        }
    }
    
    setAutomaticDateRange(range) {
        const startDateInput = document.querySelector('[data-filter-type="start_date"]');
        const endDateInput = document.querySelector('[data-filter-type="end_date"]');
        
        if (!startDateInput || !endDateInput) return;
        
        const today = new Date();
        const endDate = new Date(today);
        let startDate = new Date(today);
        
        switch (range) {
            case '7':
                startDate.setDate(today.getDate() - 7);
                break;
            case '30':
                startDate.setDate(today.getDate() - 30);
                break;
            case '90':
                startDate.setDate(today.getDate() - 90);
                break;
            case '365':
                startDate.setDate(today.getDate() - 365);
                break;
            default:
                startDate.setDate(today.getDate() - 30);
        }
        
        startDateInput.value = this.formatDateForInput(startDate);
        endDateInput.value = this.formatDateForInput(endDate);
    }
    
    formatDateForInput(date) {
        return date.toISOString().split('T')[0];
    }
    
    triggerFilterUpdate() {
        clearTimeout(this.debounceTimeout);
        this.debounceTimeout = setTimeout(() => {
            this.updateFilterState();
            if (window.dashboard) {
                window.dashboard.handleFilterChange();
            }
        }, this.debounceDelay);
    }
    
    updateFilterState() {
        const filterForm = document.getElementById('filterForm');
        if (!filterForm) return;
        
        const formData = new FormData(filterForm);
        this.filterState = {};
        
        for (let [key, value] of formData.entries()) {
            if (value) {
                if (this.filterState[key]) {
                    // Handle multiple values
                    if (!Array.isArray(this.filterState[key])) {
                        this.filterState[key] = [this.filterState[key]];
                    }
                    this.filterState[key].push(value);
                } else {
                    this.filterState[key] = value;
                }
            }
        }
        
        this.updateFilterSummary();
    }
    
    updateFilterSummary() {
        // Update any filter summary displays
        const summaryElement = document.getElementById('filterSummary');
        if (summaryElement) {
            const summary = this.generateFilterSummary();
            summaryElement.textContent = summary;
        }
    }
    
    generateFilterSummary() {
        const parts = [];
        
        if (this.filterState.date_range && this.filterState.date_range !== '30') {
            const ranges = {
                '7': 'Last 7 days',
                '90': 'Last 3 months',
                '365': 'Last year',
                'custom': 'Custom range'
            };
            parts.push(ranges[this.filterState.date_range] || 'Custom range');
        }
        
        if (this.filterState.experiments) {
            const count = Array.isArray(this.filterState.experiments) 
                ? this.filterState.experiments.length 
                : 1;
            parts.push(`${count} experiment${count > 1 ? 's' : ''}`);
        }
        
        if (this.filterState.channels) {
            const count = Array.isArray(this.filterState.channels) 
                ? this.filterState.channels.length 
                : 1;
            parts.push(`${count} channel${count > 1 ? 's' : ''}`);
        }
        
        return parts.length > 0 ? `Filtered by: ${parts.join(', ')}` : 'No filters applied';
    }
    
    resetFilters() {
        const filterForm = document.getElementById('filterForm');
        if (!filterForm) return;
        
        // Reset form to defaults
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
        
        // Handle date range display
        this.handleDateRangeChange();
        
        // Update state
        this.filterState = {};
        this.updateFilterSummary();
        
        // Trigger update
        this.triggerFilterUpdate();
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
                        option.selected = Array.isArray(value) 
                            ? value.includes(option.value) 
                            : value === option.value;
                    });
                } else {
                    element.value = value;
                }
            }
        }
        
        this.handleDateRangeChange();
        this.triggerFilterUpdate();
    }
    
    highlightActiveFilter(filterButton) {
        // Remove active class from all filter buttons
        document.querySelectorAll('.saved-filter-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        
        // Add active class to clicked button
        filterButton.classList.add('active');
    }
    
    clearActiveFilterHighlight() {
        document.querySelectorAll('.saved-filter-btn').forEach(btn => {
            btn.classList.remove('active');
        });
    }
    
    async handleSaveFilter() {
        const form = document.getElementById('saveFilterForm');
        if (!form) return;
        
        const formData = new FormData(form);
        formData.set('filter_data', JSON.stringify(this.filterState));
        
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
                this.closeModal(document.getElementById('filtersModal'));
                
                // Refresh page to show new saved filter
                setTimeout(() => window.location.reload(), 1000);
            } else {
                this.showNotification('Failed to save filter', 'error');
            }
        } catch (error) {
            console.error('Save filter error:', error);
            this.showNotification('Failed to save filter', 'error');
        }
    }
    
    async handleExport() {
        const form = document.getElementById('exportForm');
        if (!form) return;
        
        const formData = new FormData(form);
        
        // Add current filter data if requested
        if (formData.get('include_filters')) {
            formData.set('filter_data', JSON.stringify(this.filterState));
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
                    this.downloadBlob(blob, response);
                    
                    this.showNotification('Export completed successfully', 'success');
                    this.closeModal(document.getElementById('exportModal'));
                }
            } else {
                this.showNotification('Export failed', 'error');
            }
        } catch (error) {
            console.error('Export error:', error);
            this.showNotification('Export failed', 'error');
        }
    }
    
    downloadBlob(blob, response) {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = this.getFilenameFromResponse(response) || 'dashboard_export';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }
    
    getFilenameFromResponse(response) {
        const disposition = response.headers.get('Content-Disposition');
        if (disposition) {
            const match = disposition.match(/filename="(.+)"/);
            return match ? match[1] : null;
        }
        return null;
    }
    
    openModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.showModal();
        }
    }
    
    closeModal(modal) {
        if (modal && modal.close) {
            modal.close();
        }
    }
    
    loadInitialFilterState() {
        this.updateFilterState();
        
        // Check URL parameters for initial filters
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.size > 0) {
            this.applyUrlParameters(urlParams);
        }
    }
    
    applyUrlParameters(urlParams) {
        const filterForm = document.getElementById('filterForm');
        if (!filterForm) return;
        
        for (const [key, value] of urlParams.entries()) {
            const element = filterForm.querySelector(`[name="${key}"]`);
            if (element) {
                element.value = value;
            }
        }
        
        this.handleDateRangeChange();
        this.triggerFilterUpdate();
    }
    
    updateUrlWithFilters() {
        const url = new URL(window.location);
        
        // Clear existing filter parameters
        const keysToRemove = [];
        for (const key of url.searchParams.keys()) {
            if (['date_range', 'start_date', 'end_date', 'granularity', 'experiments', 'channels'].includes(key)) {
                keysToRemove.push(key);
            }
        }
        keysToRemove.forEach(key => url.searchParams.delete(key));
        
        // Add current filter parameters
        for (const [key, value] of Object.entries(this.filterState)) {
            if (Array.isArray(value)) {
                value.forEach(v => url.searchParams.append(key, v));
            } else {
                url.searchParams.set(key, value);
            }
        }
        
        // Update URL without page refresh
        window.history.replaceState({}, '', url);
    }
    
    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `alert alert-${type} fixed top-4 right-4 z-50 shadow-lg max-w-sm fade-in`;
        notification.innerHTML = `
            <div class="flex items-center gap-2">
                <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-triangle' : 'info-circle'}"></i>
                <span>${message}</span>
                <button class="btn btn-xs btn-ghost ml-auto" onclick="this.parentElement.parentElement.remove()">
                    <i class="fas fa-times"></i>
                </button>
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
    
    getCSRFToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }
    
    getFilterState() {
        return { ...this.filterState };
    }
    
    setFilterState(newState) {
        this.filterState = { ...newState };
        this.updateFilterSummary();
    }
}

// Initialize filter manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.filterManager = new FilterManager();
});