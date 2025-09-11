# Dynamic Filters Developer Guide

## Overview

The Dynamic Filters system provides a flexible way to filter data across different views in Open Chat Studio. It's implemented using a reusable filter component that can be configured with different data sources and filter options.

## How Dynamic Filters Work

Dynamic filters use a JavaScript-based frontend component combined with a Python backend filter system. The system supports:

- Multiple filter conditions with AND logic
- Various operators (equals, contains, range, etc.)
- Different field types (string, timestamp, choice)
- Date range presets
- Real-time filtering with AJAX updates

## Backend Implementation

### Filter Classes

The system is built around the `DynamicFilter` base class:

```python
from django.db.models import Q

class DynamicFilter:
    columns: list = []
    
    def apply(self):
        # Apply filters to queryset
        
    def build_filter_condition(self, column, operator, value) -> Q:
        """Returns a Q object that is used to filter the queryset with"""
        # Override in subclasses. 
```

## Template Variables (df_ prefix)

All dynamic filter template variables use the `df_` prefix. Here are all available variables:

### Core Configuration Variables

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `df_filter_data_source_url` | string | AJAX endpoint for fetching filtered data | `reverse("experiments:sessions-list", args=(team_slug, experiment_id))` |
| `df_filter_data_source_container_id` | string | DOM element ID to update with filtered results | `"sessions-table"` |
| `df_filter_columns` | list | Available columns for filtering | `ExperimentSessionFilter.columns` |
| `df_field_type_filters` | dict | Mapping of field types to available operators | `FIELD_TYPE_FILTERS` |

### Data Options Variables

These variables are only required if they pertain to a column that you configured.

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `df_date_range_options` | list | Predefined date range options | `[{"label": "Last 1 Hour", "value": "1h"}, ...]` |
| `df_available_tags` | list | Available tags for filtering | `["tag1", "tag2", "tag3"]` |
| `df_experiment_versions` | list | Available experiment versions | `["v1.0", "v1.1", "v2.0"]` |
| `df_experiment_list` | list | Available experiments | `[{"id": 1, "label": "Experiment 1"}, ...]` |
| `df_channel_list` | list | Available channels | `[{"value": "web", "label": "Web"}, ...]` |
| `df_state_list` | list | Available states/statuses | `["active", "completed", "pending"]` |
| `df_span_names` | list | Available span names (for traces) | `["span1", "span2", "span3"]` |

### Special Configuration Variables

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `df_date_range_column_name` | string | Column name for date range filtering | `"last_message"` or `"timestamp"` |

## Frontend Integration

### Including the Filter Component

Add the filter component to your template:

```html
{% include 'experiments/filters.html' %}
```

### Required JavaScript Data

The template automatically includes JSON script tags for configuration:

```html
{{ df_field_type_filters|json_script:"field-type-filters" }}
{{ df_date_range_options|json_script:"date-range-options" }}
{{ df_filter_columns|json_script:"filter-columns-data" }}
<!-- ... other data scripts ... -->
```

### JavaScript Configuration

The filter component uses Alpine.js and expects these global variables:

```javascript
const dataSourceUrl = "{{df_filter_data_source_url}}";
const dataSourceContainerId = "{{df_filter_data_source_container_id}}";
```

## Implementation Example

### 1. View Setup

In your view, provide the required template variables:

```python
class MyFilterableView(LoginAndTeamRequiredMixin, TemplateView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Required core configuration
        context.update({
            "df_filter_data_source_url": reverse("my_app:data_endpoint"),
            "df_filter_data_source_container_id": "my-data-table",
            "df_filter_columns": MyDynamicFilter.columns,
            "df_field_type_filters": FIELD_TYPE_FILTERS,
            
            # Date range support
            "df_date_range_options": DATE_RANGE_OPTIONS,
            "df_date_range_column_name": "created_at",
            
            # Optional data sources
            "df_available_tags": ["tag1", "tag2"],
            "df_experiment_list": [{"id": 1, "label": "Exp 1"}],
            "df_state_list": ["active", "inactive"],
        })
        
        return context
```

### 2. Custom Filter Class

Create a filter class for your model:

```python
class MyDynamicFilter(DynamicFilter):
    columns = [
        "name",
        "created_at", 
        "status",
        "tags"
    ]
    
    def build_filter_condition(self, column, operator, value):
        if column == "name":
            return self.build_string_filter("name", operator, value)
        elif column == "created_at":
            return self.build_timestamp_filter(operator, value, "created_at", self.timezone)
        elif column == "status":
            return self.build_choice_filter("status", operator, value)
        elif column == "tags":
            return self.build_tags_filter(operator, value)
        return None
```

### 3. AJAX Endpoint

Create an endpoint that handles filtering:

```python
class MyDataTableView(SingleTableView):
    def get_queryset(self):
        queryset = MyModel.objects.filter(team=self.request.team)
        
        # Apply dynamic filters
        if filters := self.request.GET:
            timezone = self.request.session.get("detected_tz", None)
            filter_instance = MyDynamicFilter(queryset, filters, timezone)
            queryset = filter_instance.apply()
            
        return queryset
```

### 4. Template Integration

In your template:

```html
<div class="space-y-4">
    {% include 'experiments/filters.html' %}
    
    <div id="my-data-table">
        {% include 'table/single_table.html' %}
    </div>
</div>
```

## Filter URL Parameters

Filters are passed as URL parameters with this pattern:

- `filter_{index}_column` - The column to filter on
- `filter_{index}_operator` - The operator to use
- `filter_{index}_value` - The value to filter by

Example: `?filter_0_column=name&filter_0_operator=contains&filter_0_value=test`
