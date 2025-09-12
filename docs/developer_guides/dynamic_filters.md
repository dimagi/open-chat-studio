
# Dynamic Filters

Dynamic filters provide a flexible way to filter data on the frontend and backend. This guide explains how to use and create new dynamic filters.

## How it Works

The dynamic filtering system is composed of two main parts: a backend that defines the filtering logic and a frontend that provides the user interface.

### Backend

The backend is responsible for defining the filters and applying them to the database queries. The core components are:

- **`ColumnFilter`**: An abstract base class that defines the interface for a single column filter. Each `ColumnFilter` implementation is responsible for applying a specific filter to a queryset. See `apps/web/dynamic_filters/base.py` for the definition of this class.
- **`MultiColumnFilter`**: A class that holds a list of `ColumnFilter`s and applies them to a queryset. This class is used to combine multiple filters into a single filter that can be applied to a table. See `apps/web/dynamic_filters/base.py` for the definition of this class.
- **Filter Implementations**: Concrete implementations of `ColumnFilter` can be found in `apps/web/dynamic_filters/column_filters.py` and other `filters.py` files throughout the project. For example, `apps/experiments/filters.py` contains filters specific to experiments.

### Frontend

The frontend is built using [Alpine.js](https://alpinejs.dev/) and HTMX. It dynamically generates the filter UI based on the configuration provided by the backend.

- **Filter Template**: The main filter template is `templates/experiments/filters.html`. This template contains the Alpine.js component that manages the filter state and UI.
- **Filter Configuration**: The backend provides the filter configuration to the frontend through a set of `df_*` context variables. These variables are passed as JSON scripts in the HTML and then used to initialize the Alpine.js component. See `apps/experiments/views/experiment.py` for an example of how this data is provided to the template.

## Linking query parameters to ORM operations

### Query Parameters
Filter values are passed through URL query parameters using a structured naming convention:

- **`filter_{i}_column`** - Specifies which column to filter on
- **`filter_{i}_operator`** - Defines the filter operation (e.g., equals, contains, greater than)
- **`filter_{i}_value`** - Contains the actual filter value

The `{i}` represents the filter index, allowing multiple filters to be applied simultaneously (e.g., `filter_0_column`, `filter_1_column`, etc.).

### Column Filter
The `ColumnFilter` class acts as a bridge between query parameters and ORM filters. Each filter defines a `query_param` attribute that corresponds to the column name in the query parameters. When a request contains `filter_{i}_column` matching this `query_param`, the filter processes the associated operator and value to generate the appropriate database query.

## Step-by-Step Walkthrough: Creating a Product Inventory Filter

This walkthrough will guide you through creating a complete filtering system for a hypothetical product inventory feature. We'll create a custom filter column, integrate it into a multi-column filter, and wire it up to a view.

### Step 1: Create a Custom Column Filter

First, let's create a filter for product categories. Create a new file for your app's filters:

```python
# apps/inventory/filters.py
import json
from apps.web.dynamic_filters.base import ColumnFilter, ColumnFilterData, Operators

class ProductCategoryFilter(ColumnFilter):
    """Filter products by category name."""
    query_param = "category"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None):
        """Apply category filtering to the queryset."""
        if not column_filter.value:
            return queryset

        if column_filter.operator == Operators.EQUALS:
            return queryset.filter(category__name=column_filter.value)
        elif column_filter.operator == Operators.CONTAINS:
            return queryset.filter(category__name__icontains=column_filter.value)
        elif column_filter.operator == Operators.DOES_NOT_CONTAIN:
            return queryset.exclude(category__name__icontains=column_filter.value)
        elif column_filter.operator == Operators.STARTS_WITH:
            return queryset.filter(category__name__istartswith=column_filter.value)
        elif column_filter.operator == Operators.ENDS_WITH:
            return queryset.filter(category__name__iendswith=column_filter.value)
        elif column_filter.operator == Operators.ANY_OF:
            # Parse JSON array of values
            categories = json.loads(column_filter.value)
            return queryset.filter(category__name__in=categories)
        
        return queryset
```

### Step 2: Create a Multi-Column Filter

Now let's create a multi-column filter that combines our new category filter with existing filters:

```python
# apps/inventory/filters.py (continued)
from apps.web.dynamic_filters.base import MultiColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter

class ProductInventoryFilter(MultiColumnFilter):
    """Filter for product inventory using multiple column filters."""
    
    filters: list[ColumnFilter] = [
        ProductCategoryFilter(),
        TimestampFilter(db_column="created_at", query_param="created_date"),
        TimestampFilter(db_column="updated_at", query_param="last_updated"),
        # Add more filters as needed
    ]

    def prepare_queryset(self, queryset):
        """Prepare the queryset with any necessary annotations or select_related calls."""
        return queryset.select_related('category')
```

### Step 3: Create the Model and Table (for completeness)

```python
# apps/inventory/models.py
from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

class Product(models.Model):
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

```python
# apps/inventory/tables.py
import django_tables2 as tables
from .models import Product

class ProductTable(tables.Table):
    class Meta:
        model = Product
        fields = ('name', 'category', 'price', 'stock_quantity', 'created_at', 'updated_at')
        attrs = {'class': 'table table-striped'}
```

### Step 4: Create the View

Create a view that uses our new filter system:

```python
# apps/inventory/views.py
from django.views.generic import TemplateView
from django_tables2 import SingleTableView
from apps.web.dynamic_filters.datastructures import FilterParams
from apps.experiments.filters import get_filter_context_data
from .models import Product
from .tables import ProductTable
from .filters import ProductInventoryFilter

class ProductInventoryView(SingleTableView):
    """View for displaying filtered product inventory."""
    model = Product
    table_class = ProductTable
    template_name = "inventory/product_list.html"
    paginate_by = 25

    def get_queryset(self):
        """Apply filters to the queryset."""
        queryset = super().get_queryset()
        
        # Create filter instance and apply it
        filter_params = FilterParams.from_request(self.request)
        product_filter = ProductInventoryFilter(filter_params)
        timezone = self.request.session.get("detected_tz")
        
        return product_filter.apply(queryset, timezone)

    def get_context_data(self, **kwargs):
        """Add filter configuration to the template context."""
        context = super().get_context_data(**kwargs)
        
        # Add filter context data
        filter_context = {
            "df_product_categories": ... # the available categories
        }
        context.update(filter_context)
        return context
```

### Step 5: Create the Template and Update Filters

Create the template that includes the filter interface:

```html
<!-- apps/inventory/templates/inventory/product_list.html -->
{% extends "base.html" %}
{% load django_tables2 %}

{% block title %}Product Inventory{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8">
    <h1 class="text-2xl font-bold mb-6">Product Inventory</h1>
    <!-- Include the dynamic filters -->
    {% include "experiments/filters.html" %}
    <!-- Rest of the page -->
</div>

{% endblock %}
```

**Update the Shared Filter Template**

Since the filter template is shared across the application, you'll need to update `templates/experiments/filters.html` to include your new filter columns. Add your custom columns to the `allColumns` object in the Alpine.js component:

In templates/experiments/filters.html, add the following:
```javascript
{{ df_product_categories|default:"[]"|json_script:"product-categories" }}
<script>
    const categories = JSON.parse(document.getElementById('product-categories').textContent);
    ...
    const allColumns = {
        ...
        'category': {
            type: 'string', 
            operators: fieldTypeFilters.string,
            options: categories
            label: 'Product Category'
        },
        'created_date': {
            type: 'timestamp', 
            operators: fieldTypeFilters.timestamp, 
            options: dateRangeOptions
            label: 'Created Date'
        },
        'last_updated': {
            type: 'timestamp', 
            operators: fieldTypeFilters.timestamp, 
            options: dateRangeOptions
            label: 'Last Updated'
        }
    }
    ...
</script>

```
