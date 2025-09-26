
# Dynamic Filters

Dynamic filters provide a flexible way to filter data on the frontend and backend. This guide explains how to use and create new dynamic filters.

## How it Works

The dynamic filtering system is composed of two main parts: a backend that defines the filtering logic and a frontend that provides the user interface.

### Backend

The backend is responsible for defining the filters and applying them to the database queries. The core components are:

- **`ColumnFilter`**: An abstract base class that defines the interface for a single column filter. Each `ColumnFilter` implementation processes URL parameters and applies the appropriate database filter to a queryset. The base class automatically maps operators to methods (e.g., "equals" → `apply_equals`).

- **`MultiColumnFilter`**: A container class that holds a list of `ColumnFilter` instances and applies them to a queryset. It provides a `columns()` class method to list available filter column names and handles the orchestration of applying multiple filters.

- **Filter Types**: `StringColumnFilter`, `ChoiceColumnFilter`, and `TimestampFilter` provide pre-built implementations for common filtering patterns, reducing boilerplate code.

- **Filter Implementations**: Concrete implementations of `ColumnFilter` can be found in `apps/web/dynamic_filters/column_filters.py` (base filters like `TimestampFilter`, `ParticipantFilter`) and in app-specific `filters.py` files throughout the project (e.g., `apps/experiments/filters.py`).

- **FilterParams & ColumnFilterData**: `FilterParams` extracts and organizes filter data from request query parameters into `ColumnFilterData` objects, which contain the column name, operator, and value for each filter.

### Frontend

The frontend is built using [Alpine.js](https://alpinejs.dev/) and HTMX. It dynamically generates the filter UI based on the configuration provided by the backend.

- **Filter Template**: The main filter template is `templates/experiments/filters.html`. This template contains the Alpine.js component that manages the filter state and UI.
- **Filter Configuration**: The backend provides the filter configuration to the frontend through a set of `df_*` context variables. These variables are passed as JSON scripts in the HTML and then used to initialize the Alpine.js component. See `apps/experiments/views/experiment.py` for an example of how this data is provided to the template.

## Linking query parameters to ORM operations

### Query Parameters
Filter values are passed through URL query parameters using a structured naming convention:

- **`filter_{i}_column`** - Specifies which column to filter on (matches the `query_param` of a `ColumnFilter`)
- **`filter_{i}_operator`** - Defines the filter operation (e.g., equals, contains, before, after)
- **`filter_{i}_value`** - Contains the actual filter value

The `{i}` represents the filter index (0 to `MAX_FILTER_PARAMS-1`), allowing multiple filters to be applied simultaneously (e.g., `filter_0_column`, `filter_1_column`, etc.).

### FilterParams and ColumnFilterData
The `FilterParams` class extracts filter parameters from request query parameters and organizes them into `ColumnFilterData` objects. Each `ColumnFilterData` contains the column name, operator, and value for a single filter.

### Column Filter
The `ColumnFilter` class acts as a bridge between query parameters and ORM filters. Each filter defines a `query_param` attribute that corresponds to the column name in the query parameters. When a request contains `filter_{i}_column` matching this `query_param`, the filter processes the associated operator and value to generate the appropriate database query.

The `ColumnFilter.apply()` method:
1. Retrieves the `ColumnFilterData` for its `query_param` from `FilterParams`
2. Converts the operator to a method name (e.g., "starts with" → `apply_starts_with`)
3. Calls the appropriate `apply_*` method with the parsed value

### Available Filter Types

The dynamic filter system provides several filter types that implements common filtering patterns:

#### StringColumnFilter
Provides methods for string-based filtering operations:
- `apply_equals()` - Exact match
- `apply_contains()` - Case-insensitive contains
- `apply_does_not_contain()` - Case-insensitive exclusion
- `apply_starts_with()` - Case-insensitive starts with
- `apply_ends_with()` - Case-insensitive ends with
- `apply_any_of()` - Match any value from a JSON list

Requires setting a `column` class variable with the database field path.

#### ChoiceColumnFilter
Provides methods for choice-based filtering operations:
- `apply_any_of()` - Match any value from a list
- `apply_all_of()` - Match all values from a list (AND logic)
- `apply_excludes()` - Exclude all values from a list

Requires setting a `column` class variable with the database field path.

### Available Operators

The system supports the following operators defined in the `Operators` enum:

- **String operations**: `equals`, `contains`, `does not contain`, `starts with`, `ends with`, `any of`
- **Date/time operations**: `on`, `before`, `after`, `range`
- **Choice operations**: `any of`, `all of`, `excludes`

Operators are configured in the `ColumnFilter` class. They are determined automatically based on the filter type but
can be overridden.

## Step-by-Step Walkthrough: Creating a Product Inventory Filter

This walkthrough will guide you through creating a complete filtering system for a hypothetical product inventory feature. We'll create a custom filter column, integrate it into a multi-column filter, and wire it up to a view.

### Step 1: Create a Custom Column Filter

First, let's create a filter for product categories using the existing filter types:

```python
# apps/inventory/filters.py
from apps.web.dynamic_filters.base import ChoiceColumnFilter, StringColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter

class ProductCategoryFilter(ChoiceColumnFilter):
    """Filter products by category name."""
    query_param: str = "category"
    column: str = "category__name"  # Database field path
    label: str = "Category"
    
    def prepare(self, team, **kwargs):
        self.options = [
            {"id": cat.id, "label": cat.name} 
            for cat in Category.objects.filter(team=team).all()
        ]
        
p_filter = ProductCategoryFilter()

# Alternately, you can construct it directly using kwargs:

p_filter = ChoiceColumnFilter(label="Category", query_param="category", column="category__name", options=[...])
```

### Step 2: Create a Multi-Column Filter

Now let's create a multi-column filter that combines our new category filter with existing filters:

```python
# apps/inventory/filters.py (continued)
from typing import ClassVar
from collections.abc import Sequence
from apps.web.dynamic_filters.base import MultiColumnFilter

class ProductInventoryFilter(MultiColumnFilter):
    """Filter for product inventory using multiple column filters."""
    
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ProductCategoryFilter(),
        TimestampFilter(label="Created At", column="created_at", query_param="created_date"),
        TimestampFilter(label="Updated At", column="updated_at", query_param="last_updated"),
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
        product_filter = ProductInventoryFilter()
        timezone = self.request.session.get("detected_tz")
        
        filter_params = FilterParams.from_request(self.request)
        return product_filter.apply(queryset, filter_params, timezone)

    def get_context_data(self, **kwargs):
        """Add filter configuration to the template context."""
        context = super().get_context_data(**kwargs)
        
        # Add filter context data using the helper function
        filter_context = get_filter_context_data(
            team=self.request.team,  # Assuming team is available in request
            columns=ProductInventoryFilter.columns(self.request.team),
            date_range_column="created_date",
            table_url=reverse("inventory:product_table"),  # Your HTMX table URL
            table_container_id="product-table"
        )
        
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
